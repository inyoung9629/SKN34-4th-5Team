from datetime import datetime, timedelta
from unittest.mock import Mock

from django.test import SimpleTestCase

from llm.v2.course.gate_times import gate_window
from llm.v2.course.itinerary_planner import ItineraryPlanner
from llm.v2.course.itinerary_request import ItineraryRequest, KST
from llm.v2.course.itinerary_service import render_itinerary
from .test_itinerary_planner import STADIUM, COVERAGE, GAME, NOW, place, route, request


def internal(identity="inside-food", *, scope="internal", kind="food", **extra):
    return {"id": identity, "stadium": "JAMSIL", "kind": kind, "name": identity, "scope": scope,
            "floor": "2층", "zone": "1루", "pin": None, "locationStatus": "zone_only", **extra}


def gate(minutes=120):
    return {"opens_at": (GAME - timedelta(minutes=minutes)).isoformat(), "status": "reference",
            "source": "사용자 첨부표", "evidence": "blog_reference"}


class GatePolicyTests(SimpleTestCase):
    def lookup(self, code, day="2026-10-01", home="LG", **values):
        return gate_window({"stadium_code": code, "home_team": home, "starts_at": f"{day}T18:30:00+09:00"}, request(**values))

    def test_hanwha_weekday_weekend_holiday_and_full_membership(self):
        self.assertEqual(self.lookup("DAEJEON", home="HH")["minutes_before"], 120)
        self.assertEqual(self.lookup("DAEJEON", "2026-10-03", "HH")["minutes_before"], 150)
        self.assertEqual(self.lookup("DAEJEON", "2026-10-05", "HH")["minutes_before"], 150)
        self.assertEqual(self.lookup("DAEJEON", "2026-10-05", "HH", entry_membership="hanwha_full")["minutes_before"], 180)
        self.assertEqual(self.lookup("DAEJEON", "2026-10-01", "HH", entry_membership="hanwha_full")["minutes_before"], 120)

    def test_current_year_holiday_additions_are_not_treated_as_weekdays(self):
        for day in ("2026-05-01", "2026-07-17"):
            self.assertEqual(self.lookup("DAEJEON", day, "HH")["minutes_before"], 150)

    def test_friday_rules_are_team_specific_not_generic_weekend(self):
        self.assertEqual(self.lookup("MUNHAK", "2026-10-02", "SK")["minutes_before"], 120)
        self.assertEqual(self.lookup("JAMSIL", "2026-10-02", "LG")["minutes_before"], 120)
        self.assertEqual(self.lookup("DAEGU", "2026-10-02", "SS")["minutes_before"], 90)
        self.assertEqual(self.lookup("SAJIK", "2026-10-02", "LT")["minutes_before"], 120)
        self.assertEqual(self.lookup("DAEJEON", "2026-10-02", "HH")["minutes_before"], 120)

    def test_fixed_venues_and_ssg_membership(self):
        for code in ("SUWON", "GOCHEOK", "GWANGJU"):
            self.assertEqual(self.lookup(code)["minutes_before"], 120)
        self.assertEqual(self.lookup("MUNHAK", home="SK", entry_membership="ssg_season")["minutes_before"], 150)
        self.assertEqual(self.lookup("MUNHAK", "2026-10-02", "SK", entry_membership="ssg_season")["minutes_before"], 180)

    def test_doosan_estimate_is_not_pretended_official_lg_confirmation(self):
        result = self.lookup("JAMSIL", home="OB")
        self.assertEqual(result["minutes_before"], 90)
        self.assertEqual(result["evidence"], "estimated_lg_rule_for_doosan")
        self.assertIsNone(self.lookup("JAMSIL", home="SS")["opens_at"])

    def test_nc_open_practice_is_not_used_for_general_admission(self):
        self.assertIsNone(self.lookup("CHANGWON", home="NC")["opens_at"])

    def test_unknown_days_seasons_and_membership_are_not_invented(self):
        for result in (self.lookup("DAEGU", "2026-10-12", "SS"),
                       self.lookup("SUWON", "2027-10-01", "KT"),
                       self.lookup("JAMSIL", "2026-10-01", "LG", entry_membership="hanwha_full"),
                       self.lookup("JAMSIL", "2026-10-05", "LG")):
            self.assertEqual(result["status"], "unknown")

    def test_lotte_special_games_require_an_explicit_request(self):
        self.assertEqual(self.lookup("SAJIK", home="LT")["minutes_before"], 60)
        self.assertEqual(self.lookup("SAJIK", home="LT", special_game=True)["minutes_before"], 180)

    def test_user_supplied_gate_time_for_unknown_stadium_is_labeled(self):
        result = self.lookup("CHANGWON", home="NC", gate_open_at=GAME.replace(hour=16))
        self.assertEqual(result["status"], "user_supplied")
        self.assertIsNone(self.lookup("CHANGWON", home="NC", gate_open_at=GAME + timedelta(minutes=1))["opens_at"])


class InternalItineraryTests(SimpleTestCase):
    def plan(self, spec, *, records=None, public=None, window=None, origin=None):
        self.directions = Mock(side_effect=route)
        planner = ItineraryPlanner(public or [], STADIUM, COVERAGE, self.directions,
                                   internal_records=records if records is not None else [internal()],
                                   gate=gate() if window is None else window)
        return planner.plan(spec, GAME, origin=origin, now=NOW)

    def test_inside_last_stop_uses_kickoff_without_extra_twenty_minutes_or_route(self):
        spec = request(stops=[{"kind": "food", "phase": "inside", "stay_minutes": 30}])
        result = self.plan(spec)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["internal_finish_at"], GAME.isoformat())
        self.assertEqual(result["pre_game_deadline"], GAME.isoformat())
        self.assertEqual(result["stadium_arrival_at"], (GAME - timedelta(minutes=40)).isoformat())
        self.assertEqual(result["legs"], [])
        self.directions.assert_not_called()
        self.assertEqual(result["stops"][-1]["phase"], "inside")
        self.assertIsNone(result["stops"][-1]["lat"])

    def test_external_to_stadium_is_counted_but_inside_to_stadium_is_not(self):
        spec = request(stops=[{"kind": "cafe"}, {"kind": "food", "phase": "inside"}])
        result = self.plan(spec, public=[place("외부카페", 50, "cafe")])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["legs"]), 1)
        self.assertEqual(result["legs"][0]["to"], STADIUM["placeId"])
        self.assertEqual(result["internal_finish_at"], GAME.isoformat())

    def test_internal_visits_do_not_start_before_gate_opens(self):
        spec = request(stops=[{"kind": "food", "phase": "inside"}], start_at=GAME.replace(hour=15, minute=30))
        result = self.plan(spec)
        self.assertEqual(result["status"], "ok")
        arrival = datetime.fromisoformat(result["stops"][-1]["arrive_at"])
        self.assertGreaterEqual(arrival, datetime.fromisoformat(gate()["opens_at"]))

    def test_open_alignment_adds_one_explicitly_labeled_internal_default(self):
        spec = ItineraryRequest(arrive_at_open=True)
        result = self.plan(spec)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stadium_arrival_at"], gate()["opens_at"])
        self.assertTrue(result["default_internal"])
        self.assertIn("기본 제안", render_itinerary(result, spec))

    def test_without_internal_request_no_facility_is_added(self):
        result = self.plan(request(), public=[place("외부식당", 50)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["pre_game_deadline"], (GAME - timedelta(minutes=20)).isoformat())
        self.assertFalse(any(s["phase"] == "inside" for s in result["stops"]))

    def test_gate_window_too_short_does_not_shorten_user_stay(self):
        spec = request(stops=[{"kind": "food", "phase": "inside", "stay_minutes": 60}])
        result = self.plan(spec, window=gate(minutes=60))
        self.assertEqual(result["status"], "gate_window_too_short")
        self.assertEqual(result["stops"], [])

    def test_unknown_gate_and_unknown_scope_fail_separately(self):
        spec = request(stops=[{"kind": "food", "phase": "inside"}])
        self.assertEqual(self.plan(spec, window={"opens_at": None})["status"], "gate_unavailable")
        self.assertEqual(self.plan(spec, records=[internal(scope="unknown")])["status"], "internal_scope_unverified")
        self.assertEqual(self.plan(spec, records=[internal(scope="exterior")])["status"], "internal_no_match")

    def test_specific_facility_uses_internal_catalogue_and_preserves_floor(self):
        spec = request(stops=[{"kind": "facility", "phase": "inside", "required_keywords": ["굿즈"]}])
        result = self.plan(spec, records=[internal("굿즈샵", kind="facility")])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][-1]["name"], "굿즈샵")
        self.assertEqual(result["stops"][-1]["floor"], "2층")

    def test_no_outside_reentry_sequence_is_silently_reordered(self):
        with self.assertRaises(ValueError):
            request(stops=[{"kind": "food", "phase": "inside"}, {"kind": "cafe", "phase": "before"}])

    def test_multiple_inside_stops_have_no_outdoor_routes_and_finish_at_kickoff(self):
        spec = request(stops=[{"kind": "food", "phase": "inside"}, {"kind": "facility", "phase": "inside"}])
        result = self.plan(spec, records=[internal(), internal("굿즈", kind="facility")])
        self.assertEqual(result["status"], "ok")
        self.directions.assert_not_called()
        self.assertEqual(result["internal_finish_at"], GAME.isoformat())
        self.assertEqual(len(result["stops"]), 3)
