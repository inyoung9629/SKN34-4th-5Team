from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict, messages_to_dict

from llm.serializer.message import done_payload, _validate_context
from llm.tests.test_course_editing import CURRENT, GAME, PLACES, REPLACEMENT, plan
from llm.v1.rag.course import agent, editing, memory
from llm.v2.agent.course_output import public_course


class ConversationEditingTest(SimpleTestCase):
    def test_per_leg_modes_keep_other_segments_and_roundtrip_to_map(self):
        changed = self.edit(plan("transport", [], leg_changes=[{"start": "cafe", "end": "game", "mode": "transit"}]))
        key = editing.leg_key(PLACES[1], PLACES[2])
        self.assertEqual(changed["legModes"], {key: "transit"})
        self.assertEqual(changed["travel"]["mode"], "walk")
        self.assertEqual(public_course(changed)["legModes"], {key: "transit"})
        self.assertEqual([p["placeId"] for p in changed["places"]], ["1", "2", "3", "4"])
        invalid = self.edit(plan("transport", [], leg_changes=[{"start": "food", "end": "game", "mode": "car"}]))
        self.assertEqual(invalid["places"], [])

    def test_compound_replace_move_duration_rebuilds_once_and_undo_is_atomic(self):
        batch = plan("batch", [], actions=[plan("replace", ["cafe"]),
            plan("move", ["cafe"], reference="food", position="before"),
            plan("duration", [], durations=[{"visit_id": "cafe", "minutes": 20}])])
        with patch.object(editing, "candidates", return_value=[REPLACEMENT]), patch.object(editing, "choose", return_value=REPLACEMENT), \
                patch.object(editing, "rebuild", wraps=editing.rebuild) as rebuild:
            changed = self.edit(batch)
        rebuild.assert_called_once()
        self.assertEqual([p["placeId"] for p in changed["places"]], ["99", "1", "3", "4"])
        self.assertEqual(changed["places"][0]["stayMin"], 20)
        restored = self.edit(plan("undo", []), changed["courseMemory"]["current"], changed["courseMemory"])
        self.assertEqual([p["placeId"] for p in restored["places"]], ["1", "2", "3", "4"])

    def test_compound_failure_rolls_back_places_and_memory(self):
        batch = plan("batch", [], actions=[plan("remove", ["cafe"]), plan("replace", ["food"])],
                     preferences=[{"scope": "FOOD", "text": "한식 제외"}])
        with patch.object(editing, "candidates", return_value=[]), patch.object(editing, "rebuild") as rebuild:
            failed = self.edit(batch)
        rebuild.assert_not_called()
        self.assertEqual(failed["places"], [])
        self.assertEqual(failed["courseMemory"], memory.empty())
        self.assertIn("전체 변경을 취소", failed["answer"])

    def test_origin_change_preserves_visits_title_and_undo_restores_old_origin(self):
        old, new = {"lat": 37.52, "lng": 127.08, "name": "이전 출발지"}, {"lat": 37.53, "lng": 127.09}
        current = {**deepcopy(CURRENT), "writerState": {"title": "내 코스", "origin": old, "completed": True}}
        with patch("llm.v1.rag.course.arrival.resolve_origin", return_value=(new, "새 출발지", "")):
            changed = editing.answer("출발지만 변경", [], current, "JAMSIL", old, parsed=plan("origin", [], origin_query="새 출발지"))
        self.assertEqual(changed["writerState"]["title"], "내 코스")
        self.assertEqual(changed["writerState"]["origin"], {**new, "name": "새 출발지"})
        self.assertEqual([p["placeId"] for p in changed["places"]], ["1", "2", "3", "4"])
        restored = editing.answer("취소", [], changed["courseMemory"]["current"], "JAMSIL", changed["origin"],
                                  changed["courseMemory"], plan("undo", []))
        self.assertEqual(restored["writerState"]["origin"], old)
        cleared = editing.answer("출발지 삭제", [], current, "JAMSIL", old, parsed=plan("origin", [], clear_origin=True))
        self.assertIsNone(cleared["writerState"]["origin"])

    def test_station_and_shop_names_do_not_switch_stadium_before_origin_edit(self):
        for question in ("출발지만 삼성역으로 바꿔줘", "롯데월드에서 출발", "잠실역에서 출발"):
            self.assertIsNone(agent.detect_stadium(question))
        self.assertEqual(agent.detect_stadium("삼성 홈경기 코스"), "DAEGU")
        with patch.object(editing, "interpret", return_value=plan("origin", [], origin_query="삼성역")), \
                patch("llm.v1.rag.course.arrival.resolve_origin", return_value=({"lat": 37.508, "lng": 127.063}, "삼성역", "")):
            result = agent.answer("출발지만 삼성역으로 바꿔줘", hint_stadium="JAMSIL", current_course=CURRENT)
        self.assertEqual(result["stadiumCode"], "JAMSIL")
        self.assertEqual([p["placeId"] for p in result["places"]], ["1", "2", "3", "4"])

    def setUp(self):
        for name, value in (("route_legs", lambda points, *args: [{"minutes": 5, "meters": 400, "by": "walk"}] * (len(points) - 1)),):
            patcher = patch.object(editing, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("load_schedule", ({}, 1)), ("find_game", (GAME, False, [])), ("stadium_anchor", PLACES[2])):
            patcher = patch.object(agent, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def edit(self, operation, current=CURRENT, saved=None):
        return editing.answer("수정", [], deepcopy(current), "JAMSIL", None, saved, operation)

    def test_remove_only_requested_place_then_undo_restores_it(self):
        removed = self.edit(plan("remove", ["cafe"]))
        self.assertEqual([p["placeId"] for p in removed["places"]], ["1", "3", "4"])
        self.assertEqual(removed["courseMemory"]["rejected"], [])
        restored = self.edit(plan("undo", []), removed["courseMemory"]["current"], removed["courseMemory"])
        self.assertEqual([p["placeId"] for p in restored["places"]], ["1", "2", "3", "4"])
        self.assertNotIn("undo", restored["courseMemory"])

    def test_delete_last_activity_can_leave_stadium_and_add_again(self):
        removed = self.edit(plan("remove", ["food", "cafe", "park"]))
        public = public_course(removed)
        self.assertEqual([p["category"] for p in public["places"]], ["STADIUM"])
        self.assertEqual(len(_validate_context({"currentCourse": removed["courseMemory"]["current"]})["currentCourse"]["places"]), 1)
        with patch.object(editing, "candidates", return_value=[REPLACEMENT]), patch.object(editing, "choose", return_value=REPLACEMENT):
            added = self.edit(plan("add", [], category="CAFE"), removed["courseMemory"]["current"], removed["courseMemory"])
        self.assertEqual([p["placeId"] for p in added["places"]], ["99", "3"])

    def test_add_convenience_before_stadium_preserves_every_other_place(self):
        shop = {**REPLACEMENT, "name": "CU 테스트점", "category": "CONVENIENCE"}
        with patch.object(editing, "candidates", return_value=[shop]), patch.object(editing, "choose", return_value=shop):
            added = self.edit(plan("add", [], category="CONVENIENCE", reference="game"))
        self.assertEqual([p["placeId"] for p in added["places"]], ["1", "2", "99", "3", "4"])
        self.assertEqual(added["places"][2]["stayMin"], 10)
        self.assertEqual(added["places"][2]["phase"], "BEFORE")
        self.assertEqual(len({p["visitId"] for p in added["places"]}), 5)

    def test_durations_survive_reordering_and_wire_roundtrip(self):
        changed = self.edit(plan("duration", [], durations=[{"visit_id": "food", "minutes": 30}, {"visit_id": "cafe", "minutes": 60}]))
        self.assertEqual([p["stayMin"] for p in changed["places"]][:2], [30, 60])
        current = _validate_context({"currentCourse": changed["courseMemory"]["current"]})["currentCourse"]
        reordered = self.edit(plan("swap", ["food", "cafe"]), current, changed["courseMemory"])
        self.assertEqual([p["stayMin"] for p in reordered["places"]][:2], [60, 30])
        self.assertEqual([p["time"] for p in reordered["places"]][:3], ["16:30", "17:35", "18:10"])

    def test_invalid_multi_duration_is_atomic(self):
        result = self.edit(plan("duration", [], durations=[{"visit_id": "food", "minutes": 30}, {"visit_id": "game", "minutes": 60}]))
        self.assertEqual(result["places"], [])
        self.assertNotIn("stayOverride", CURRENT["places"][0])

    def test_lock_blocks_replace_delete_but_allows_reordering_and_unlock(self):
        locked = self.edit(plan("lock", ["food"]))["courseMemory"]
        for op in ("replace", "remove"):
            result = self.edit(plan(op, ["food"]), saved=locked)
            self.assertEqual(result["places"], [])
            self.assertIn("먼저", result["answer"])
        moved = self.edit(plan("swap", ["food", "cafe"]), saved=locked)
        self.assertEqual(len(moved["places"]), 4)
        self.assertEqual(self.edit(plan("unlock", ["food"]), saved=locked)["courseMemory"]["locked"], [])

    def test_rejected_place_and_conditions_survive_unsuccessful_search_and_later_turns(self):
        requested = plan("replace", ["cafe"], reject_targets=["cafe"], preferences=[{"scope": "CAFE", "text": "카페는 스타벅스"}])
        with patch.object(editing, "candidates", return_value=[]):
            failed = self.edit(requested)
        saved = failed["courseMemory"]
        self.assertEqual(saved["rejected"][0]["placeId"], "2")
        self.assertEqual(saved["conditions"][0]["text"], "카페는 스타벅스")
        with patch.object(editing, "candidates", return_value=[REPLACEMENT]) as search, patch.object(editing, "choose", return_value=REPLACEMENT) as choose:
            result = self.edit(plan("replace", ["cafe"]), saved=saved)
        self.assertIn("카페는 스타벅스", choose.call_args.args[1]["conditions"])
        self.assertIn(saved["rejected"][0], search.call_args.args[3])
        self.assertEqual(result["courseMemory"]["conditions"], saved["conditions"])
        cleared = self.edit(plan("preferences", [], forget_conditions=["카페는 스타벅스"], allow_names=[PLACES[1]["name"]]), saved=saved)
        self.assertEqual(cleared["courseMemory"]["conditions"], [])
        self.assertEqual(cleared["courseMemory"]["rejected"], [])

    def test_undo_rejects_manual_changes_and_restores_modes_and_preferences(self):
        current = deepcopy(CURRENT)
        key = editing.leg_key(PLACES[0], PLACES[1])
        current["legModes"] = {key: "car"}
        saved = {**memory.empty(), "conditions": [{"scope": "CAFE", "text": "카페는 스타벅스"}]}
        changed = self.edit(plan("preferences", [], forget_conditions=["카페는 스타벅스"], mode="transit"), current, saved)
        after = deepcopy(changed["courseMemory"]["current"])
        after["places"][0]["name"] = "수동으로 변경"
        blocked = self.edit(plan("undo", []), after, changed["courseMemory"])
        self.assertEqual(blocked["places"], [])
        undone = self.edit(plan("undo", []), changed["courseMemory"]["current"], changed["courseMemory"])
        self.assertEqual(undone["legModes"], {key: "car"})
        self.assertEqual(undone["travel"]["mode"], "walk")
        self.assertEqual(undone["courseMemory"]["conditions"], saved["conditions"])

    def test_completed_checkpoint_memory_survives_reload_without_public_snapshots(self):
        state = {**memory.empty(), "conditions": [{"scope": "CAFE", "text": "카페는 스타벅스"}], "undo": {"private": "PRIVATE"}}
        human, answer = HumanMessage("기억", id="h"), AIMessage("기억했어요", id="a")
        call = AIMessage("", tool_calls=[{"name": "plan_course", "id": "c", "args": {"request": "기억"}}])
        tool = ToolMessage("기억", name="plan_course", tool_call_id="c", artifact={"course_memory": state})
        messages = messages_from_dict(messages_to_dict([human, call, tool, answer]))
        turns = {"h": {"status": "completed", "answer_id": "a"}}
        self.assertEqual(memory.restore(messages, turns), state)
        public = done_payload(messages, turns)
        self.assertEqual(public["coursePreferences"]["conditions"], ["카페는 스타벅스"])
        self.assertNotIn("PRIVATE", str(public))
        self.assertEqual(memory.restore(messages, {"h": {"status": "failed"}}), {})
        self.assertEqual(memory.restore(messages, {"h": {"status": "completed", "answer_deleted": True}}), {})
        self.assertEqual(memory.restore([], {}), {})

    def test_server_memory_used_when_chat_has_no_map_snapshot(self):
        saved = {**memory.empty(), "current": deepcopy(CURRENT)}
        with patch.object(editing, "interpret", return_value=plan("remove", ["cafe"])):
            result = agent.answer("카페 빼줘", course_memory=saved)
        self.assertEqual([p["placeId"] for p in result["places"]], ["1", "3", "4"])

    def test_one_time_replacement_does_not_create_preferences_or_rejections(self):
        original = {**memory.empty(), "conditions": [{"scope": "FOOD", "text": "한식"}]}
        result = editing.update_memory(original, plan("replace", ["cafe"], conditions=["스타벅스 브랜드"]), CURRENT)
        self.assertEqual(memory.relevant(result, "CAFE"), [])
        self.assertEqual(memory.relevant(result, "FOOD"), ["한식"])
        self.assertEqual(result["rejected"], [])
        self.assertEqual(original, result)

    def test_one_time_brand_override_applies_now_and_restores_persistent_preference_next_turn(self):
        saved = {**memory.empty(), "conditions": [{"scope": "CAFE", "text": "카페는 스타벅스"}]}
        once = plan("replace", ["cafe"], conditions=["메가커피 브랜드"],
            request_preferences=[{"scope": "CAFE", "text": "메가커피 브랜드"}],
            request_overrides=["카페는 스타벅스"])
        with patch.object(editing, "candidates", return_value=[REPLACEMENT]), patch.object(editing, "choose", return_value=REPLACEMENT) as choose:
            changed = self.edit(once, saved=saved)
        self.assertEqual(choose.call_args.args[1]["conditions"], ["메가커피 브랜드"])
        self.assertEqual(changed["courseMemory"]["conditions"], saved["conditions"])
        self.assertEqual(changed["courseMemory"]["rejected"], [])
        with patch.object(editing, "candidates", return_value=[PLACES[1]]) as candidates, patch.object(editing, "choose", return_value=PLACES[1]) as choose:
            self.edit(plan("replace", ["cafe"]), changed["courseMemory"]["current"], changed["courseMemory"])
        self.assertEqual(choose.call_args.args[1]["conditions"], ["카페는 스타벅스"])
        self.assertNotIn(PLACES[1]["placeId"], [p.get("placeId") for p in candidates.call_args.args[3]])

    def test_failed_one_time_search_does_not_claim_to_remember_or_blacklist(self):
        with patch.object(editing, "candidates", return_value=[]):
            result = self.edit(plan("replace", ["cafe"], conditions=["일회성 브랜드"]))
        self.assertEqual(result["courseMemory"], memory.empty())
        self.assertNotIn("기억했어요", result["answer"])

    def test_generation_applies_one_time_conditions_without_saving_them(self):
        once = plan("new", [], request_preferences=[{"scope": "FOOD", "text": "이번에는 일식만"}])
        generated = {"places": deepcopy(PLACES), "stadiumCode": "JAMSIL", "travel": {"mode": "walk"}}
        with patch.object(editing, "interpret", return_value=once), patch.object(agent, "_answer", return_value=generated) as generate:
            result = agent.answer("이번에는 일식만 넣어 새로 짜줘", course_memory=memory.empty(), hint_stadium="JAMSIL")
        self.assertEqual(memory.relevant(generate.call_args.kwargs["course_memory"], "FOOD"), ["이번에는 일식만"])
        self.assertEqual(result["courseMemory"]["conditions"], [])

    def test_fresh_origin_and_corridor_searches_keep_conditions_and_rejections(self):
        origin, anchor = {"lat": 37.512, "lng": 127.085}, PLACES[2]
        base = {"category": "CAFE", "lat": 37.512, "lng": 127.079,
                "detail": "카페 > 커피", "dist": .3, "distance": 700}
        rejected = {**base, "name": "스타벅스 제외 지점", "placeId": "old"}
        wrong = {**base, "name": "다른 브랜드 카페", "placeId": "wrong"}
        wanted = {**base, "name": "스타벅스 새 지점", "placeId": "new"}
        state = {**memory.empty(), "conditions": [{"scope": "CAFE", "text": "스타벅스만"}],
                 "rejected": [memory.identity(rejected)]}
        for segments in (None, [(origin, anchor)]):
            with self.subTest(corridor=bool(segments)):
                cache = {}
                apply_memory = lambda items: agent.remembered_candidates(items, state, cache)
                with patch.object(agent, "_kakao_step", return_value=[rejected, wrong, wanted]), \
                     patch.object(editing, "choose", return_value=wanted) as choose:
                    steps = agent.build_origin_course(origin, anchor, [], agent.slots.parse("경기 전에 카페만"),
                                                      False, segments, candidate_filter=apply_memory)
                self.assertEqual([s["place"]["placeId"] for s in steps if s["place"]], ["new"])
                choose.assert_called_once()
                self.assertEqual([p["placeId"] for p in choose.call_args.args[0]], ["wrong", "new"])
                self.assertEqual(choose.call_args.args[1]["conditions"], ["스타벅스만"])

    def test_remembered_itinerary_never_duplicates_current_requested_visits(self):
        state = {**memory.empty(), "conditions": [
            {"scope": "ITINERARY", "text": "경기 전 식사와 카페, 경기 후 산책"},
            {"scope": "CAFE", "text": "카페는 스타벅스만"},
        ]}
        for question, expected in (
            ("잠실 기준 경기 전에 식사와 카페, 경기 후 산책 코스", (["FOOD", "CAFE"], ["WALK"])),
            ("이번엔 사직으로 다시 짜줘", (["FOOD", "CAFE"], ["WALK"])),
            ("경기 전에 카페 두 곳만 갈래", (["CAFE", "CAFE"], [])),
        ):
            with self.subTest(question=question):
                self.assertEqual(agent.plan_steps(memory.planning_slots(question, state), False), expected)
