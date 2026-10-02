from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase

from baseball.models import Game, ScheduleDay, Stadium, Team
from llm.enum import ChatRole, MessageStatus
from llm.models import ChatSession
from llm.serializer.message import ChatMessageInputSerializer
from llm.service.chat import message_delete, message_update, send_message
from llm.v2.course.conditions import ConditionPatch
from llm.v2.course.games import GameRepository, KST, snapshot, get_game_range_freshness as stored_range_freshness
from llm.v2.course.policy import resolve_course
from llm.v2.course.state import empty_state, merge_patch

NOW = datetime(2026, 9, 28, 12, tzinfo=KST)


class ConditionTest(SimpleTestCase):
    def test_reject_invalid_ranges_and_conflicting_teams(self):
        for values in ({"date_from": "2026-10-02", "date_to": "2026-10-01"},
                       {"game_time_min": "19:00", "game_time_max": "18:00"},
                       {"game_time_min": "19:00+09:00"},
                       {"team_code": "LG", "opponent_code": "LG"},
                       {"game_id": True}, {"game_id": 2 ** 100}, {"single_game": "false"},
                       {"team_code": "UNKNOWN"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ConditionPatch.model_validate(values)

    def test_date_patch_and_clear_are_atomic(self):
        state, _ = merge_patch(None, ConditionPatch(date_from=date(2026, 10, 1)))
        self.assertEqual(state["conditions"]["date_to"], "2026-10-01")
        state, _ = merge_patch(state, ConditionPatch(clear_fields=["date_from"]))
        self.assertNotIn("date_to", state["conditions"])

    def test_ui_cannot_supply_server_state_profile_or_actions(self):
        for forged in ({"profile": "allow"}, {"selected_game": {}}, {"action": "edit_past"}):
            serializer = ChatMessageInputSerializer(data={"content": "코스", "context": {"course": forged}})
            self.assertFalse(serializer.is_valid())
        serializer = ChatMessageInputSerializer(data={"content": "코스", "context": {
            "course_runtime": {"profile_team": "LG"}, "course_pending": True,
            "course": {"team_code": "OB", "date_from": "2026-10-01"}}})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(set(serializer.validated_data["context"]), {"course"})


class CoursePolicyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lg = Team.objects.create(id=1, team_code="LG", team_name_ko="LG 트윈스")
        cls.ob = Team.objects.create(id=2, team_code="DOOSAN", team_name_ko="두산 베어스")
        cls.ss = Team.objects.create(id=3, team_code="SAMSUNG", team_name_ko="삼성 라이온즈")
        cls.jamsil = Stadium.objects.create(id=1, stadium_code="JAMSIL", stadium_name_ko="잠실야구장",
            address="서울", latitude=37.5, longitude=127, geocode_source="test", collected_at=NOW)
        cls.daegu = Stadium.objects.create(id=2, stadium_code="DAEGU", stadium_name_ko="대구 삼성 라이온즈 파크",
            address="대구", latitude=35.8, longitude=128.6, geocode_source="test", collected_at=NOW)
        # 공급자의 경기 없는 날도 검증된 empty marker로 저장한다.
        start, end = date(2026, 9, 1), date(2026, 12, 31)
        ScheduleDay.objects.bulk_create([ScheduleDay(date=start + timedelta(days=i), status="empty",
            game_count=0, game_codes=[], source_fetched_at=NOW, last_synced_at=NOW)
            for i in range((end - start).days + 1)])

    def setUp(self):
        fresh = patch("llm.v2.course.games.get_game_range_freshness", return_value={"stale": False})
        self.fresh = fresh.start()
        self.addCleanup(fresh.stop)
        self.repo = GameRepository(clock=lambda: NOW)

    def game(self, pk=1, *, day="2026-09-29", at="18:30", home=None, away=None,
             stadium=None, status="scheduled", active=True):
        row = Game.objects.create(id=pk, game_code=f"game-{pk}", source_external_code=f"external-{pk}",
            home_team=home or self.lg, away_team=away or self.ob, stadium=stadium or self.jamsil,
            game_date=date.fromisoformat(day), game_time=time.fromisoformat(at), status_code=status,
            game_type="regular", collected_at=NOW, source="tving", source_fetched_at=NOW, last_synced_at=NOW)
        if active:
            marker = ScheduleDay.objects.get(date=row.game_date)
            marker.game_codes.append(row.source_external_code)
            marker.game_count += 1
            marker.status = "ready"
            marker.save()
        return row

    def resolve(self, values=None, previous=None, **kwargs):
        return resolve_course("테스트 요청", previous, now=kwargs.pop('now', NOW),
            extractor=lambda *args: ConditionPatch.model_validate(values or {}), **kwargs)

    def test_missing_target_asks_and_keeps_other_preferences(self):
        result = self.resolve({"preferences": {"food": "일식", "companion": "여자친구"}})
        self.assertIsNone(result.anchor)
        self.assertEqual(result.state["pending"], "target")
        self.assertEqual(result.state["preferences"]["food"], "일식")
        self.fresh.assert_not_called()

    def test_stadium_only_uses_earliest_future_eligible_game(self):
        self.game(1, day="2026-09-28", at="11:00")
        self.game(2, day="2026-09-28", at="12:00")
        self.game(3, day="2026-09-28", at="13:00", status="cancelled")
        self.game(4, day="2026-09-28", at="14:00", status="live")
        self.game(5, day="2026-09-28", at="15:00", status="UNKNOWN")
        self.game(6, day="2026-09-28", at="16:00", active=False)
        self.game(7, day="2026-09-28", at="18:00")
        result = self.resolve({"stadium_code": "JAMSIL"})
        self.assertEqual(result.anchor["id"], 7)
        self.assertIn("한국 시간", result.message)

    def test_team_only_returns_nearest_home_and_away_with_actual_stadium(self):
        self.game(1, home=self.ob, away=self.lg, stadium=self.daegu)
        self.game(2, day="2026-09-30", home=self.ss, away=self.ob, stadium=self.daegu)
        result = self.resolve({"team_code": "OB"})
        self.assertIsNone(result.anchor)
        self.assertEqual([g["id"] for g in result.state["candidates"]], [1, 2])
        self.assertEqual(result.state["candidates"][0]["stadium_code"], "DAEGU")

    def test_team_and_stadium_intersection_not_team_home_guess(self):
        self.game(1, home=self.ss, away=self.lg, stadium=self.daegu)
        self.game(2, day="2026-09-30", home=self.ss, away=self.lg)
        result = self.resolve({"team_code": "SS", "stadium_code": "JAMSIL"})
        self.assertEqual(result.anchor["id"], 2)

    def test_opponent_and_time_and_date_filters_are_conjunctive(self):
        self.game(1, at="14:00")
        self.game(2, at="19:00", away=self.ss)
        self.game(3, day="2026-09-30", at="19:00")
        result = self.resolve({"team_code": "LG", "opponent_code": "OB", "game_time_min": "18:00",
            "date_from": "2026-09-29", "date_to": "2026-09-30"})
        self.assertEqual(result.anchor["id"], 3)

    def test_home_away_filtered_before_limit(self):
        for pk in range(1, 24):
            self.game(pk, day=(date(2026, 9, 29) + timedelta(days=pk)).isoformat())
        self.game(25, day="2026-10-25", home=self.ob, away=self.lg)
        result = self.resolve({"team_code": "LG", "home_away": "away"})
        self.assertEqual(result.anchor["id"], 25)

    def test_explicit_next_single_and_home_side_choose_one(self):
        self.game(1)
        self.game(2, day="2026-09-30", home=self.ob, away=self.lg)
        for extra in ({"single_game": True}, {"home_away": "home"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.resolve({"team_code": "LG", **extra}).anchor["id"], 1)

    def test_one_side_in_explicit_window_does_not_expand_dates(self):
        self.game(1)
        self.game(2, day="2026-09-30", home=self.ob, away=self.lg)
        result = self.resolve({"team_code": "LG", "date_from": "2026-09-29"})
        self.assertEqual(len(result.state["candidates"]), 1)
        self.assertIn("한쪽 후보", result.message)

    def test_no_match_and_stale_are_distinct_and_keep_filters(self):
        result = self.resolve({"stadium_code": "JAMSIL", "date_from": "2026-09-29"})
        self.assertEqual(result.state["pending"], "no_match")
        self.fresh.return_value = {"stale": True}
        result = self.resolve(previous=result.state)
        self.assertEqual(result.state["pending"], "schedule_unavailable")
        self.assertEqual(result.state["conditions"]["date_from"], "2026-09-29")

    def test_missing_calendar_is_not_no_games(self):
        ScheduleDay.objects.filter(date="2026-09-29").delete()
        result = self.resolve({"stadium_code": "JAMSIL", "date_from": "2026-09-29"})
        self.assertEqual(result.state["pending"], "schedule_unavailable")

    def test_rolling_window_selects_known_nearest_without_whole_month(self):
        self.game()
        ScheduleDay.objects.filter(date__gte="2026-09-30").delete()
        result = self.resolve({"stadium_code": "JAMSIL"})
        self.assertEqual(result.anchor["id"], 1)

    def test_rolling_window_does_not_skip_missing_day_or_fake_no_match(self):
        self.game(day="2026-09-30")
        ScheduleDay.objects.filter(date="2026-09-29").delete()
        result = self.resolve({"stadium_code": "JAMSIL"})
        self.assertIsNone(result.anchor)
        self.assertEqual(result.state["pending"], "schedule_unavailable")

    def test_requested_day_freshness_does_not_require_rest_of_month(self):
        self.game()
        ScheduleDay.objects.filter(date__gte="2026-09-30").delete()
        self.assertFalse(stored_range_freshness(date(2026, 9, 29), date(2026, 9, 29))["stale"])
        self.assertTrue(stored_range_freshness(date(2026, 9, 29), date(2026, 9, 30))["stale"])
        query, problem = self.repo._fresh_query(date(2026, 9, 29), date(2026, 9, 29))
        self.assertIsNone(problem)
        self.assertEqual(query.count(), 1)

    def test_missing_stadium_blocks_selection(self):
        game = self.game()
        Game.objects.filter(pk=game.pk).update(stadium=None)
        result = self.resolve({"team_code": "LG", "single_game": True})
        self.assertIsNone(result.anchor)
        self.assertEqual(result.state["pending"], "schedule_unavailable")

    def test_source_stadium_resolves_new_game_with_no_csv_stadium(self):
        game = self.game()
        Game.objects.filter(pk=game.pk).update(stadium=None, source_stadium_name="잠실")
        result = self.resolve({"stadium_code": "JAMSIL"})
        self.assertEqual(result.anchor["stadium_code"], "JAMSIL")
        game.refresh_from_db()
        self.assertIsNone(game.stadium_id)  # 추천이 수집 데이터 자체를 변경하지 않는다.

    def test_source_stadium_overrides_stale_csv_venue(self):
        game = self.game()
        Game.objects.filter(pk=game.pk).update(source_stadium_name="대구")
        result = self.resolve({"stadium_code": "DAEGU"})
        self.assertEqual(result.anchor["id"], game.pk)
        self.assertEqual(result.anchor["stadium_code"], "DAEGU")
        result = self.resolve({"stadium_code": "JAMSIL", "date_from": "2026-09-29"})
        self.assertEqual(result.state["pending"], "no_match")

    def test_unknown_source_stadium_is_not_assumed_to_be_team_home(self):
        game = self.game()
        Game.objects.filter(pk=game.pk).update(source_stadium_name="확인되지 않은 제2구장")
        result = self.resolve({"team_code": "LG", "single_game": True})
        self.assertIsNone(result.anchor)
        self.assertEqual(result.state["pending"], "schedule_unavailable")

    def test_time_tie_requires_selection_not_pk_order(self):
        self.game(1)
        self.game(2)
        result = self.resolve({"stadium_code": "JAMSIL"})
        self.assertIsNone(result.anchor)
        self.assertEqual(len(result.state["candidates"]), 2)
        result = self.resolve({"game_id": 2}, previous=result.state)
        self.assertEqual(result.anchor["id"], 2)

    def test_doubleheader_order_and_specific_second_game(self):
        self.game(1, at="14:00")
        self.game(2, at="18:00")
        self.assertEqual(self.resolve({"stadium_code": "JAMSIL"}).anchor["id"], 1)
        self.assertEqual(self.resolve({"game_id": 2}).anchor["id"], 2)

    def test_game_id_only_is_remembered_and_next_game_keeps_actual_venue(self):
        self.game(1)
        self.game(2, day="2026-09-30")
        first = self.resolve({"game_id": 1})
        self.assertEqual(self.resolve(previous=first.state).anchor["id"], 1)
        Game.objects.filter(pk=1).update(status_code="final")
        following = self.resolve({"action": "next_game"}, previous=first.state)
        self.assertEqual(following.anchor["id"], 2)
        self.assertIn("이전 경기의 구장", following.message)

    def test_new_target_drops_old_selected_game_id(self):
        self.game(1)
        self.game(2, stadium=self.daegu)
        first = self.resolve({"game_id": 1})
        result = self.resolve({"stadium_code": "DAEGU"}, previous=first.state)
        self.assertEqual(result.anchor["id"], 2)

    def test_arrival_preference_is_not_game_time_filter(self):
        self.game()
        result = self.resolve({"stadium_code": "JAMSIL", "preferences": {"arrival": "15시"}})
        self.assertEqual(result.anchor["time"], "18:30:00")

    def test_kst_boundary_does_not_use_utc_date(self):
        self.game(day="2026-09-29", at="00:30")
        result = self.repo.search({"stadium_code": "JAMSIL"}, datetime.fromisoformat("2026-09-28T16:00:00+00:00"))
        self.assertEqual(result.games, [])

    def test_profile_requires_confirmation_and_exposes_source(self):
        self.game()
        result = self.resolve({"date_from": "2026-09-29"}, profile_team="LG")
        self.assertIsNone(result.anchor)
        self.assertIn("마이페이지", result.message)
        result = self.resolve({"action": "confirm"}, previous=result.state, profile_team="SS")
        self.assertEqual(result.anchor["id"], 1)
        self.assertEqual(result.state["conditions"]["team_code"], "LG")

    def test_explicit_stadium_ignores_profile(self):
        self.game()
        result = self.resolve({"stadium_code": "JAMSIL"}, profile_team="SS")
        self.assertNotIn("team_code", result.state["conditions"])
        self.assertEqual(result.anchor["id"], 1)

    def test_explicit_team_clear_is_not_refilled_by_profile(self):
        state, _ = merge_patch(None, ConditionPatch(team_code="LG"))
        result = self.resolve({"clear_fields": ["team_code"]}, previous=state, profile_team="LG")
        self.assertEqual(result.state["pending"], "target")
        self.assertNotIn("team_code", result.state["conditions"])

    def test_stadium_clear_is_not_replaced_with_profile_team(self):
        state, _ = merge_patch(None, ConditionPatch(stadium_code="JAMSIL"))
        result = self.resolve({"clear_fields": ["stadium_code"]}, previous=state, profile_team="LG")
        self.assertEqual(result.state["pending"], "target")
        self.assertNotIn("team_code", result.state["conditions"])

    def test_profile_opt_out_removes_only_profile_derived_team(self):
        self.game()
        result = self.resolve(profile_team="LG")
        result = self.resolve({"profile": "deny"}, previous=result.state, profile_team="LG")
        self.assertNotIn("team_code", result.state["conditions"])
        self.assertEqual(result.state["pending"], "target")

    def test_preferences_followup_keeps_locked_game(self):
        self.game()
        first = self.resolve({"stadium_code": "JAMSIL", "preferences": {"food": "일식"}})
        result = self.resolve({"preferences": {"activities": "카페"}}, previous=first.state)
        self.assertEqual(result.anchor["id"], 1)
        self.assertEqual(result.state["preferences"], {"food": "일식", "activities": "카페"})

    def test_target_change_clears_anchor_but_keeps_explicit_date(self):
        self.game()
        first = self.resolve({"stadium_code": "JAMSIL", "date_from": "2026-09-29"})
        result = self.resolve({"stadium_code": "DAEGU"}, previous=first.state)
        self.assertIsNone(result.anchor)
        self.assertEqual(result.state["conditions"]["date_from"], "2026-09-29")

    def test_ambiguous_candidate_followup_does_not_choose(self):
        self.game()
        self.game(2, home=self.ob, away=self.lg, day="2026-09-30")
        first = self.resolve({"team_code": "LG"})
        result = self.resolve({"preferences": {"activities": "카페 변경"}}, previous=first.state)
        self.assertIsNone(result.anchor)
        self.assertEqual(result.state["pending"], "choice")
        result = self.resolve({"choice": "away"}, previous=result.state)
        self.assertEqual(result.anchor["id"], 2)

    def test_expired_anchor_asks_then_explicit_next_game_reselects(self):
        first_game = self.game()
        self.game(2, day="2026-09-30")
        first = self.resolve({"stadium_code": "JAMSIL", "date_from": "2026-09-29"})
        Game.objects.filter(pk=first_game.pk).update(status_code="final")
        expired = self.resolve(previous=first.state)
        self.assertEqual(expired.state["pending"], "expired")
        self.assertIsNone(expired.anchor)
        result = self.resolve({"action": "next_game"}, previous=expired.state)
        self.assertEqual(result.anchor["id"], 2)

    def test_changed_schedule_requires_confirmation(self):
        game = self.game()
        first = self.resolve({"stadium_code": "JAMSIL"})
        Game.objects.filter(pk=game.pk).update(game_time=time(19))
        result = self.resolve(previous=first.state)
        self.assertIsNone(result.anchor)
        self.assertIn("변경", result.message)
        result = self.resolve({"action": "confirm"}, previous=result.state)
        self.assertEqual(result.anchor["time"], "19:00:00")

    def test_reschedule_conflicting_with_explicit_time_is_not_silently_accepted(self):
        game = self.game()
        first = self.resolve({"stadium_code": "JAMSIL", "game_time_min": "18:30", "game_time_max": "18:30"})
        Game.objects.filter(pk=game.pk).update(game_time=time(19))
        result = self.resolve(previous=first.state)
        self.assertIsNone(result.anchor)
        self.assertEqual(result.state["pending"], "changed")
        result = self.resolve({"action": "confirm"}, previous=result.state)
        self.assertIsNone(result.anchor)
        self.assertEqual(result.state["conditions"]["game_time_max"], "18:30:00")

    def test_month_refresh_crossing_start_time_does_not_select_started_game(self):
        self.game(1, day="2026-09-28", at="12:01")
        self.game(2, day="2026-09-28", at="18:30")
        repo = GameRepository(clock=lambda: NOW + timedelta(minutes=2))
        result = repo.search({"stadium_code": "JAMSIL"}, NOW)
        self.assertEqual(result.games[0]["id"], 2)

    def test_recall_and_edit_past_are_explicit_historical_operations(self):
        self.game()
        first = self.resolve({"stadium_code": "JAMSIL"})
        self.fresh.reset_mock()
        recalled = self.resolve({"action": "recall"}, previous=first.state)
        self.assertIsNone(recalled.anchor)
        self.fresh.assert_not_called()
        edited = self.resolve({"action": "edit_past"}, previous=first.state)
        self.assertEqual(edited.anchor["id"], 1)
        self.assertFalse(edited.historical)  # A future game must still be revalidated.
        past_now = datetime.fromisoformat(first.anchor['starts_at']) + timedelta(days=1)
        edited = self.resolve({"action": "edit_past"}, previous=first.state, now=past_now)
        self.assertTrue(edited.historical)
        self.assertIn("새 직관 일정 추천이 아니", edited.message)

    def test_empty_context_does_not_reset_and_repeated_screen_does_not_override_text(self):
        self.game()
        self.game(2, stadium=self.daegu)
        first = self.resolve(context={"stadium": "잠실야구장"})
        second = self.resolve({"stadium_code": "DAEGU"}, previous=first.state, context={"stadium": "잠실야구장"})
        third = self.resolve(previous=second.state, context={"stadium": "잠실야구장"})
        self.assertEqual(third.anchor["stadium_code"], "DAEGU")
        self.assertEqual(self.resolve(previous=third.state, context={}).anchor["stadium_code"], "DAEGU")

    def test_only_changed_ui_fields_override_room_conditions(self):
        first = self.resolve(context={"course": {"stadium_code": "JAMSIL", "team_code": "LG"}})
        second = self.resolve({"stadium_code": "DAEGU"}, previous=first.state)
        third = self.resolve(previous=second.state, context={"course": {"stadium_code": "JAMSIL", "team_code": "OB"}})
        self.assertEqual(third.state["conditions"]["team_code"], "OB")
        self.assertEqual(third.state["conditions"]["stadium_code"], "DAEGU")

    def test_ambiguous_and_invalid_extraction_do_not_invoke_schedule(self):
        result = self.resolve({"clarification": "서울 어느 구장인가요?"}, profile_team="LG")
        self.assertIsNone(result.anchor)
        self.fresh.assert_not_called()

    def test_bare_confirmation_cannot_resolve_ambiguous_target_with_profile(self):
        first = self.resolve({"clarification": "서울 어느 구장인가요?"})
        result = self.resolve({"action": "confirm"}, previous=first.state, profile_team="LG")
        self.assertEqual(result.state["pending"], "conditions")
        self.fresh.assert_not_called()
        result = resolve_course("모호한 문장", profile_team="LG", now=NOW,
            extractor=lambda *a: ConditionPatch(team_code="invalid"))
        self.assertEqual(result.state["pending"], "conditions")
        self.fresh.assert_not_called()


from llm.tests.course_checkpoint import CourseCheckpointTestCase


class CourseMemoryTest(CourseCheckpointTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="course-user", team_code="OB")
        self.session = ChatSession.objects.create(user=self.user)
        self.request = SimpleNamespace(user=self.user, COOKIES={})

    def fake(self, state=None, fail=False):
        from langchain_core.messages import AIMessage
        captured = {}

        class Graph:
            def stream(self, inputs, **kwargs):
                captured.update(inputs)
                yield (), "custom", {"course_delta": "답변"}
                if fail:
                    raise RuntimeError("test")
                yield (), "updates", {"course": {"messages": [AIMessage("답변")], "course_state": state}}
        return Graph(), captured

    def send(self, state):
        fake, captured = self.fake(state)
        with patch("llm.v2.agent.chain.get_graph", return_value=fake):
            frames = list(send_message(self.session, "질문", version="v2"))
            self.assertEqual(frames[-1][0], "done", frames)
        return captured

    def second_question(self):
        return [m for m in self.snapshot(self.session)[0] if m.type == "human"][1]

    def test_server_profile_snapshot_and_room_isolation(self):
        state, _ = merge_patch(None, ConditionPatch(stadium_code="JAMSIL"))
        captured = self.send(state)
        self.assertEqual(captured["course_runtime"]["profile_team"], "OB")
        self.assertIsNone(captured["course_runtime"]["state"])
        self.assertEqual(self.send(None)["course_runtime"]["state"], state)
        self.session = ChatSession.objects.create(user=self.user)
        self.assertIsNone(self.send(None)["course_runtime"]["state"])

    def test_message_delete_restores_previous_snapshot(self):
        first, _ = merge_patch(None, ConditionPatch(team_code="LG"))
        second, _ = merge_patch(first, ConditionPatch(team_code="OB"))
        self.send(first)
        self.send(second)
        message_delete(self.request, self.session.pk, self.second_question().id)
        self.assertEqual(self.send(None)["course_runtime"]["state"], first)

    def test_edit_restores_state_before_edited_turn(self):
        first, _ = merge_patch(None, ConditionPatch(team_code="LG"))
        self.send(first)
        self.send(empty_state())
        target = self.second_question()
        fake, captured = self.fake()
        with patch("llm.v2.agent.chain.get_graph", return_value=fake):
            list(message_update(self.request, self.session.pk, target.id, "수정", version="v2"))
        self.assertEqual(captured["course_runtime"]["state"], first)

    def test_failed_turn_does_not_persist_new_memory(self):
        first, _ = merge_patch(None, ConditionPatch(team_code="LG"))
        self.send(first)
        fake, _ = self.fake(empty_state(), fail=True)
        with patch("llm.v2.agent.chain.get_graph", return_value=fake):
            self.assertEqual(list(send_message(self.session, "질문"))[-1][0], "error")
        self.assertEqual(self.send(None)["course_runtime"]["state"], first)

    def test_newer_turn_prevents_late_older_stream_from_overwriting_state(self):
        old_state, _ = merge_patch(None, ConditionPatch(team_code="LG"))
        new_state, _ = merge_patch(None, ConditionPatch(team_code="OB"))
        old_fake, _ = self.fake(old_state)
        new_fake, _ = self.fake(new_state)
        with patch("llm.v2.agent.chain.get_graph", return_value=old_fake):
            old_stream = send_message(self.session, "이전 요청")
            self.assertEqual(next(old_stream)[0], "delta")
        with patch("llm.v2.agent.chain.get_graph", return_value=new_fake):
            list(send_message(self.session, "새 요청"))
        # develop은 동시 턴 둘 다 보존한다. 기억은 완료 시점이 아니라 질문 순서가 기준이다.
        self.assertEqual(list(old_stream)[-1][0], "done")
        self.assertEqual(self.send(None)["course_runtime"]["state"], new_state)

    def test_room_delete_cascades_memory_and_does_not_resurrect_stream(self):
        self.send(empty_state())
        fake, _ = self.fake(empty_state())
        with patch("llm.v2.agent.chain.get_graph", return_value=fake):
            stream = send_message(self.session, "질문")
            next(stream)
        with self.captureOnCommitCallbacks(execute=True):
            self.session.delete()
        self.assertEqual(list(stream)[-1][0], "stopped")
        self.assertFalse(ChatSession.objects.exists())

    def test_guest_has_no_profile(self):
        self.session = ChatSession.objects.create(guest=uuid4())
        self.assertIsNone(self.send(None)["course_runtime"]["profile_team"])
