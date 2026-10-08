from datetime import date, time
from unittest.mock import patch

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from baseball.models import Game, Player, StandingHistory, Team, TeamProfile, TeamTopPlayer
from llm.tools.baseball import create_baseball_domain_tools
from tving.relational import read_athlete, read_team


class ToolDataCompletenessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.team, _ = Team.objects.get_or_create(team_code="DOOSAN", defaults={"id": 980001, "team_name_ko": "두산"})
        now = timezone.now()
        Player.objects.bulk_create([Player(external_code=str(980000 + n), team=cls.team, name="동명이인" if n >= 101 else f"선수{n}") for n in range(103)])
        cls.player = Player.objects.get(external_code="980101")
        cls.player.profile_last_synced_at = cls.player.detail_last_synced_at = now
        cls.player.profile_source_fetched_at = cls.player.detail_source_fetched_at = now
        cls.player.save()
        TeamProfile.objects.create(team=cls.team, external_code="OB", source_fetched_at=now, last_synced_at=now)
        for n, (day, clock, status) in enumerate([
            (6, time(17), "scheduled"), (6, time(18, 30), "scheduled"),
            (6, time(19), "cancelled"), (6, time(20), "final"),
            (6, time.min, "scheduled"), (7, time(1), "READY"),
        ]):
            Game.objects.create(id=980000+n, game_code=f"DATA-{n}", game_date=date(2099, 10, day), game_time=clock, status_code=status, game_type="regular", collected_at=now, home_team=cls.team)

    def setUp(self):
        self.tools = {tool.name: tool for tool in create_baseball_domain_tools()}

    def test_v1_model_visible_tools_execute_expanded_and_legacy_arguments(self):
        from langchain_core.messages import AIMessage, ToolMessage
        from llm.v1.rag.domain_tools import run_model, tools_for
        visible = {tool.name: tool for tool in tools_for("assistant")}
        self.assertIs(visible["get_standings"].args_schema, self.tools["get_standings"].args_schema)
        self.assertIs(visible["get_games"].args_schema, self.tools["get_games"].args_schema)
        self.assertTrue({"as_of", "snapshot_date", "team_code", "include_detail"} <= visible["get_standings"].args.keys())
        self.assertTrue({"upcoming_only", "as_of", "team_code", "date_from", "date_to"} <= visible["get_games"].args.keys())

        class Model:
            def bind_tools(model, tools, **kwargs):
                model.tools = {tool.name: tool for tool in tools}
                return model

            def invoke(model, messages, **kwargs):
                if any(isinstance(message, ToolMessage) for message in messages):
                    model.results = [message.content for message in messages if isinstance(message, ToolMessage)]
                    return AIMessage(content="done")
                return AIMessage(content="", tool_calls=[
                    {"name": "get_standings", "args": {"as_of": "2000-01-01", "team_code": "OB", "include_detail": True}, "id": "standing"},
                    {"name": "get_games", "args": {"date_from": "2099-10-06", "date_to": "2099-10-07", "team": "두산", "upcoming_only": True, "as_of": "2099-10-06T09:00:00Z", "limit": 1}, "id": "game"},
                ])

        import json
        model = Model()
        with patch("tving.service.get_game_range_freshness", return_value={"stale": False, "warning": None}):
            self.assertEqual(run_model(model, [], "assistant").content, "done")
        standings, games = map(json.loads, model.results)
        self.assertEqual(standings["team_details"][0]["data"], read_team("OB"))
        self.assertEqual(games["items"][0]["game_code"], "DATA-1")
        self.assertEqual(games["games"][0]["home_team"], self.team.team_name_ko)
        StandingHistory.objects.create(id=980011, team=self.team, snapshot_date=date(2099, 1, 1), rank=2, wins=1, losses=0, draws=0, games_behind="0", collected_at=timezone.now())
        legacy = visible["get_standings"].invoke({"as_of": "2099-01-02"})
        self.assertEqual(legacy["actual_date"], "2099-01-01")
        self.assertEqual(legacy["standings"][0]["team_name_ko"], self.team.team_name_ko)

    def test_course_links_use_frontend_source_id_or_uuid(self):
        from travel.models import Course
        from llm.tools.travel import create_travel_tools
        tools = {tool.name: tool for tool in create_travel_tools()}
        for source in (None, "sample/한글?x#fragment"):
            course = Course.objects.create(title="링크 회귀", stadium="잠실", source_id=source)
            item = tools["get_course"].invoke({"course_id": str(course.pk)})["item"]
            expected = f"/routes/{course.pk}" if source is None else "/routes/sample%2F%ED%95%9C%EA%B8%80%3Fx%23fragment"
            self.assertEqual(item["detailPath"], expected)
            self.assertEqual(item["id"], str(course.pk))
            found = tools["search_courses"].invoke({"query": "링크 회귀"})["items"]
            self.assertEqual(next(row for row in found if row["id"] == str(course.pk))["detailPath"], expected)

    def test_filter_before_limit_duplicates_page_and_detail(self):
        result = self.tools["search_players"].invoke({"name": "동명이인", "limit": 1})
        self.assertEqual(result["total_count"], 2)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["items"][0]["externalCode"], "980101")
        self.assertEqual(self.tools["search_players"].invoke({"name": "동명이인", "offset": 2})["items"], [])
        detail = self.tools["search_players"].invoke({"team_code": "DOOSAN", "player_code": "980101"})["items"][0]
        self.assertEqual(detail["detail"], read_athlete("980101"))
        self.assertEqual(detail["detailPath"], "/standings/players/980101")
        missing = self.tools["search_players"].invoke({"player_code": "980102"})["items"][0]
        self.assertIsNone(missing["detail"])
        self.assertFalse(missing["detail_available"])

    def test_bulk_detail_queries_are_bounded(self):
        with patch("tving.service.refresh_team", return_value={"stale": False, "warning": None}), CaptureQueriesContext(connection) as queries:
            self.tools["search_players"].invoke({"team_code": "OB", "include_detail": True, "limit": 100})
        self.assertLessEqual(len(queries), 8)

    def test_team_without_standings_and_time_provenance(self):
        result = self.tools["get_standings"].invoke({"snapshot_date": "2000-01-01", "team_code": "DOOSAN", "include_detail": True})
        self.assertEqual(result["items"], [])
        entry = result["team_details"][0]
        self.assertEqual(entry["data"], read_team("OB"))
        self.assertEqual(entry["detailPath"], "/standings/teams/OB")
        self.assertIn("not_requested_snapshot", entry["time_basis"])
        self.assertIn("not_upcoming", entry["schedule_scope"])

    def test_team_detail_on_ranking_keeps_partial_top_and_exact_player_image(self):
        now = timezone.now()
        StandingHistory.objects.create(id=980010, team=self.team, snapshot_date=date(2099, 1, 1), rank=2, wins=1, losses=0, draws=0, games_behind="0", collected_at=now)
        player = Player.objects.get(pk=self.player.pk)
        player.image_url = "https://example.com/exact-player.jpg"
        player.save()
        profile = TeamProfile.objects.get(team=self.team)
        profile.top_keys = {"hitter": [["도루", player.pk]]}
        profile.save()
        TeamTopPlayer.objects.create(team=self.team, player=player, athlete_type="hitter", category="도루", rank=1, value="10", source_fetched_at=now, last_synced_at=now)
        result = self.tools["get_standings"].invoke({"snapshot_date": "2099-01-01", "team_code": "OB", "include_detail": True})
        self.assertEqual(result["team_details"], [])
        athletes = result["items"][0]["teamDetail"]["data"]["rankings"]["hitter"][0]["athletes"]
        self.assertEqual(len(athletes), 1)
        self.assertEqual(athletes[0]["imageUrl"], player.image_url)
        self.assertEqual(result["items"][0]["rank"], 2)

    def test_upcoming_kst_status_midnight_boundary_and_paging(self):
        args = {"start_date": "2099-10-06", "end_date": "2099-10-07", "upcoming_only": True, "as_of": "2099-10-06T09:00:00Z"}
        with patch("tving.service.get_game_range_freshness", return_value={"stale": False, "warning": None}):
            for cutoff, expected in (
                ("2099-10-05T23:59:00+09:00", ["DATA-4", "DATA-0", "DATA-1", "DATA-5"]),
                ("2099-10-06T00:00:00+09:00", ["DATA-0", "DATA-1", "DATA-5"]),
                ("2099-10-06T00:01:00+09:00", ["DATA-0", "DATA-1", "DATA-5"]),
            ):
                with self.subTest(as_of=cutoff):
                    boundary = self.tools["get_games"].invoke({**args, "as_of": cutoff})
                    self.assertEqual([r["game_code"] for r in boundary["items"]], expected)
                    self.assertTrue(all(r["time_precision"] == "time" for r in boundary["items"]))
                    if "DATA-4" in expected:
                        self.assertEqual(boundary["items"][0]["game_time"], "00:00:00")
            result = self.tools["get_games"].invoke(args)
            self.assertEqual([r["game_code"] for r in result["items"]], ["DATA-1", "DATA-5"])
            self.assertEqual(result["items"][0]["time_precision"], "time")
            self.assertIn("home_starting_pitcher", result["items"][0])
            page = self.tools["get_games"].invoke({**args, "offset": 1, "limit": 1})
            self.assertEqual(page["items"][0]["game_code"], "DATA-5")
            self.assertFalse(page["has_more"])
            self.assertEqual(result["total_count"], 2)
            self.assertTrue(self.tools["get_games"].invoke({**args, "limit": 1})["has_more"])
            late = self.tools["get_games"].invoke({**args, "as_of": "2099-10-06T23:59:00+09:00"})
            self.assertEqual([r["game_code"] for r in late["items"]], ["DATA-5"])
            empty = self.tools["get_games"].invoke({**args, "as_of": "2099-10-08T00:00:00+09:00"})
            self.assertEqual(empty["count"], 0)
        self.assertIsInstance(self.tools["get_games"].invoke({**args, "as_of": "2099-10-06T18:00:00"}), str)
        for args in ({"name": "선수", "offset": -1}, {"name": "선수", "limit": 101}):
            self.assertIsInstance(self.tools["search_players"].invoke(args), str)
