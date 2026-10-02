"""Offline evidence, budgets, food intent and full planner integration. No paid calls."""
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from llm.v2.course.food_requirements import FoodRequirements
from llm.v2.course.food_verification import (
    FoodSearchAnswer, FoodSearchPolicy, FoodVerifier, observed_web_sources, public_url, search_food,
)
from llm.v2.course.internal_visits import choose_internal
from llm.v2.course.itinerary_planner import ItineraryPlanner
from llm.v2.course.itinerary_request import StopRequest
from llm.v2.course.itinerary_service import generate_itinerary, render_itinerary
from .test_itinerary_planner import COVERAGE, GAME, NOW, STADIUM, place, request, route

URL = "https://example.com/branch/menu"


def need(*groups):
    return FoodRequirements.model_validate({"all_of": [
        {"any_of": [{"kind": "menu", "name": name} for name in names]} for names in (groups or [("돈까스",)])]})


def answer(requirements, verdict="pass", **updates):
    data = {"identity": "match", "identity_urls": [URL], "scope": "external", "scope_urls": [URL],
            "findings": [{"condition_id": key, "verdict": verdict, "evidence_urls": [URL]}
                         for key in requirements.conditions()],
            "evidence": [{"url": URL, "kind": "official", "published_on": None,
                          "current_menu": True, "summary": "가상의 해당 지점 메뉴 증거"}]}
    data.update(updates)
    return FoodSearchAnswer.model_validate(data)


def provider(candidate, requirements, **kwargs):
    return answer(requirements), {URL}, 1


def food_request(requirements=None, **extra):
    return request(stops=[{"kind": "food", "food": (requirements or need()).model_dump()}], **extra)


class FoodIntentTests(SimpleTestCase):
    def test_and_or_unknown_truth_tables(self):
        spec = need(("돈까스", "우동"), ("초밥",))
        self.assertEqual(spec.evaluate({"0.0": "unknown", "0.1": "pass", "1.0": "pass"}), "pass")
        self.assertEqual(spec.evaluate({"0.0": "fail", "0.1": "unknown", "1.0": "pass"}), "unknown")
        self.assertEqual(spec.evaluate({"0.0": "fail", "0.1": "fail", "1.0": "pass"}), "fail")
        self.assertEqual(spec.evaluate({}), "unknown")

    def test_menu_qualifier_is_not_a_restaurant_exclusion(self):
        stop = StopRequest(kind="food", food={"all_of": [{"any_of": [
            {"kind": "menu", "name": "돈까스", "qualifiers": ["치즈 없는"]}]}]})
        self.assertFalse(stop.food.conditions()["0.0"].exclude)
        self.assertEqual(stop.excluded_keywords, [])

    def test_legacy_cuisine_migration_is_idempotent(self):
        stop = StopRequest(kind="food", cuisine="일식")
        restored = StopRequest.model_validate(stop.model_dump())
        self.assertEqual(len(restored.food.all_of), 1)
        self.assertEqual(restored.food.conditions()["0.0"].kind, "cuisine")

    def test_schema_rejects_wrong_kind_blank_food_and_model_coordinates(self):
        for values in ({"kind": "walk", "food": need().model_dump()},
                       {"kind": "food", "food": {"all_of": []}},
                       {"kind": "food", "food": {"all_of": [{"any_of": [{"kind": "menu", "name": " "}]}]}},
                       {"kind": "food", "food": need().model_dump(), "lat": 37}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                StopRequest.model_validate(values)

    def test_inside_menu_is_preserved_but_not_searched(self):
        stop = StopRequest(kind="food", phase="inside", food=need())
        self.assertEqual(choose_internal([stop], [])[1], "internal_details_unverified")


class FoodEvidenceTests(SimpleTestCase):
    def verify(self, reply=None, *, sources=None, calls=1, candidate=None, requirements=None):
        requirements = requirements or need()
        lookup = Mock(return_value=(reply or answer(requirements), {URL} if sources is None else sources, calls))
        verifier = FoodVerifier(lookup=lookup, now=NOW)
        return verifier.verify(candidate or place("식당"), requirements)

    def test_pass_keeps_evidence_and_check_time(self):
        report = self.verify()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["checked_at"], NOW.isoformat())
        self.assertEqual(report["sources"][0]["url"], URL)

    def test_model_only_source_url_does_not_count(self):
        self.assertEqual(self.verify(sources=set())["status"], "unknown")

    def test_no_completed_web_call_is_unknown(self):
        self.assertEqual(self.verify(calls=0)["status"], "unknown")

    def test_different_or_unknown_branch_never_passes(self):
        for identity in ("mismatch", "unknown"):
            with self.subTest(identity=identity):
                self.assertEqual(self.verify(answer(need(), identity=identity))["status"], "unknown")

    def test_unknown_scope_does_not_mean_external(self):
        self.assertEqual(self.verify(answer(need(), scope="unknown"))["reason"], "restaurant_scope_unverified")

    def test_internal_scope_is_not_external_food(self):
        self.assertEqual(self.verify(answer(need(), scope="internal"))["reason"], "internal_restaurant")

    def test_local_internal_affiliation_avoids_paid_search(self):
        lookup = Mock()
        verifier = FoodVerifier(lookup=lookup, now=NOW)
        report = verifier.verify(place("내부", stadiumAffiliation={"scope": "internal"}), need())
        self.assertEqual(report["status"], "fail")
        lookup.assert_not_called()

    def test_absent_menu_finding_is_unknown_not_mismatch(self):
        self.assertEqual(self.verify(answer(need(), verdict="unknown"))["status"], "unknown")

    def test_excluded_menu_unknown_never_becomes_pass(self):
        spec = need()
        spec.all_of[0].any_of[0].exclude = True
        self.assertEqual(self.verify(answer(spec, verdict="unknown"), requirements=spec)["status"], "unknown")

    def test_actual_negative_evidence_can_fail(self):
        self.assertEqual(self.verify(answer(need(), verdict="fail"))["status"], "fail")

    def test_missing_or_duplicate_condition_ids_fail_closed(self):
        data = answer(need()).model_dump()
        for findings in ([], data["findings"] * 2, [{**data["findings"][0], "condition_id": "9.9"}]):
            with self.subTest(findings=findings):
                self.assertEqual(self.verify(answer(need(), findings=findings))["status"], "unknown")

    def test_uncited_condition_cannot_pass_on_identity_evidence_alone(self):
        findings = [{"condition_id": "0.0", "verdict": "pass", "evidence_urls": []}]
        self.assertEqual(self.verify(answer(need(), findings=findings))["status"], "unknown")

    def test_old_future_or_undated_noncurrent_sources_are_unknown(self):
        for published, current in ((NOW.date() - timedelta(days=181), True),
                                   (NOW.date() + timedelta(days=1), True), (None, False)):
            data = answer(need()).model_dump()
            data["evidence"][0].update(published_on=published, current_menu=current)
            with self.subTest(published=published):
                self.assertEqual(self.verify(FoodSearchAnswer.model_validate(data))["status"], "unknown")

    def test_recent_review_alone_is_not_menu_proof(self):
        data = answer(need()).model_dump()
        data["evidence"][0].update(kind="review", published_on=NOW.date())
        self.assertEqual(self.verify(FoodSearchAnswer.model_validate(data))["status"], "unknown")

    def test_metadata_not_generated_json_provides_provenance(self):
        urls, calls = observed_web_sources({"output": [
            {"type": "web_search_call", "status": "completed", "action": {"sources": [{"url": URL}]}},
            {"type": "message", "content": [{"text": '{"url":"https://invented.example/menu"}', "annotations": []}]}]})
        self.assertEqual(urls, {URL})
        self.assertEqual(calls, 1)

    def test_citation_links_reject_credentials_local_and_script_urls(self):
        for value in ("javascript:alert(1)", "https://localhost/menu", "http://127.0.0.1/menu",
                      "http://169.254.169.254/", "https://user:secret@example.com/", "https://example.com/a\nb"):
            with self.subTest(value=value):
                self.assertFalse(public_url(value))


class FoodBudgetTests(SimpleTestCase):
    def test_request_cache_keys_include_food_constraints(self):
        lookup = Mock(side_effect=provider)
        verifier = FoodVerifier(lookup=lookup, now=NOW)
        for food in (need(), need(), need(("우동",))):
            verifier.verify(place("식당"), food)
        self.assertEqual(lookup.call_count, 2)
        another = FoodVerifier(lookup=lookup, now=NOW)
        another.verify(place("식당"), need())
        self.assertEqual(lookup.call_count, 3)

    def test_max_four_lookups_even_when_model_fails(self):
        lookup = Mock(side_effect=TimeoutError("private upstream body"))
        verifier = FoodVerifier(lookup=lookup, now=NOW)
        for i in range(6):
            report = verifier.verify(place(str(i)), need())
        self.assertEqual(lookup.call_count, 4)
        self.assertEqual(report["reason"], "food_search_limited")
        self.assertNotIn("private", str(verifier.audit()))

    def test_remaining_budget_reduces_timeout_and_late_success_is_discarded(self):
        elapsed = [0.0]
        timeouts = []

        def slow(candidate, requirements, **kwargs):
            timeouts.append(kwargs["timeout"])
            elapsed[0] += 25
            return provider(candidate, requirements)

        verifier = FoodVerifier(lookup=slow, clock=lambda: elapsed[0], now=NOW)
        verifier.verify(place("a"), need())
        verifier.verify(place("b"), need())
        report = verifier.verify(place("c"), need())
        verifier.verify(place("d"), need())
        self.assertEqual(timeouts, [25, 25, 10])
        self.assertEqual(report["reason"], "food_search_limited")
        self.assertEqual(verifier.lookups, 3)

    def test_auth_failure_does_not_retry_other_candidates(self):
        error = type("AuthenticationError", (Exception,), {})
        lookup = Mock(side_effect=error("secret"))
        verifier = FoodVerifier(lookup=lookup, now=NOW)
        verifier.verify(place("a"), need())
        verifier.verify(place("b"), need())
        self.assertEqual(lookup.call_count, 1)

    def test_provider_uses_existing_model_and_bounded_nonpersistent_request(self):
        parsed = answer(need())
        response = SimpleNamespace(status="completed", output_parsed=parsed, model_dump=lambda **kwargs: {
            "output": [{"type": "web_search_call", "status": "completed", "action": {"sources": [{"url": URL}]}}]})
        client = Mock()
        client.responses.parse.return_value = response
        with patch("openai.OpenAI") as constructor, patch("llm.v2.agent.common.llm", return_value=SimpleNamespace(model_name="configured-model")):
            constructor.return_value.__enter__.return_value = client
            search_food({**place("가상식당"), "user_id": "private-user", "chat_history": "private-chat"}, need(), timeout=12, max_tool_calls=2)
        args = client.responses.parse.call_args.kwargs
        self.assertEqual(args["model"], "configured-model")
        self.assertEqual(args["max_tool_calls"], 2)
        self.assertFalse(args["store"])
        self.assertNotIn("private", args["input"])
        self.assertNotIn("lat", json.loads(args["input"])["restaurant"])
        self.assertEqual(constructor.call_args.kwargs, {"timeout": 12, "max_retries": 0})


class FoodPlannerTests(SimpleTestCase):
    def plan(self, candidates, lookup=provider, spec=None, *, directions=route, verifier=None):
        verifier = verifier or FoodVerifier(lookup=lookup, now=NOW)
        planner = ItineraryPlanner(candidates, STADIUM, COVERAGE, directions, food_verifier=verifier)
        return planner.plan(spec or food_request(), GAME, now=NOW)

    def test_name_and_coarse_category_need_not_contain_menu(self):
        result = self.plan([place("상호만", 50, cuisine="기타")])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["food_verification"]["status"], "pass")
        self.assertEqual(result["stops"][0]["lat"], place("상호만", 50)["lat"])

    def test_first_complete_verified_course_stops_paid_search(self):
        lookup = Mock(side_effect=provider)
        result = self.plan([place(str(i), i * 10) for i in range(1, 6)], lookup)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(lookup.call_count, 1)

    def test_unknown_candidate_is_skipped_and_radius_can_expand(self):
        def lookup(candidate, requirements, **kwargs):
            return answer(requirements, "unknown" if candidate["name"] == "near" else "pass"), {URL}, 1
        result = self.plan([place("near", 50), place("far", 220)], lookup)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["name"], "far")
        self.assertEqual(result["stops"][0]["search_radius_m"], 300)

    def test_missing_evidence_does_not_call_directions_or_return_partial_stops(self):
        lookup = lambda candidate, requirements, **kwargs: (answer(requirements, "unknown"), {URL}, 1)
        directions = Mock(side_effect=route)
        result = self.plan([place("a", 20)], lookup, directions=directions)
        self.assertEqual(result["status"], "food_unverified")
        self.assertEqual(result["stops"], [])
        directions.assert_not_called()

    def test_budget_exhaustion_is_not_no_match(self):
        lookup = lambda candidate, requirements, **kwargs: (answer(requirements, "fail"), {URL}, 1)
        result = self.plan([place(str(i), i * 10) for i in range(6)], lookup)
        self.assertEqual(result["status"], "food_search_limited")
        self.assertEqual(result["food_search"]["lookups"], 4)

    def test_confirmed_food_still_must_pass_time_validation(self):
        result = self.plan([place("a", 50)], spec=food_request(start_at=GAME.replace(hour=18)))
        self.assertEqual(result["status"], "time_infeasible")
        self.assertEqual(result["stops"], [])

    def test_unrelated_hard_constraints_are_not_silently_removed(self):
        lookup = Mock(side_effect=provider)
        result = self.plan([place("a", 50)], lookup, spec=food_request(unverified_requirements=["1만원 이하"]))
        self.assertEqual(result["status"], "constraints_unverified")
        lookup.assert_not_called()

    def test_later_web_search_time_is_included_in_depart_now(self):
        elapsed = [0.0]

        def slow(candidate, requirements, **kwargs):
            elapsed[0] += 20
            return provider(candidate, requirements)

        verifier = FoodVerifier(lookup=slow, now=NOW, clock=lambda: elapsed[0])
        planner = ItineraryPlanner([place("a", 50)], STADIUM, COVERAGE, route,
                                   food_verifier=verifier, clock=lambda: elapsed[0])
        spec = request(stops=[{"kind": "cafe"}, {"kind": "food", "phase": "after", "food": need().model_dump()}], start_now=True)
        planner.places.append(place("cafe", 30, "cafe"))
        result = planner.plan(spec, GAME, now=NOW)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["start_at"], (NOW + timedelta(seconds=20)).isoformat())

    def test_later_web_search_cannot_return_a_now_past_fixed_start(self):
        elapsed = [0.0]

        def slow(candidate, requirements, **kwargs):
            elapsed[0] += 20
            return provider(candidate, requirements)

        verifier = FoodVerifier(lookup=slow, now=NOW, clock=lambda: elapsed[0])
        planner = ItineraryPlanner([place("a", 50), place("cafe", 30, "cafe")], STADIUM, COVERAGE, route,
                                   food_verifier=verifier, clock=lambda: elapsed[0])
        spec = request(stops=[{"kind": "cafe"}, {"kind": "food", "phase": "after", "food": need().model_dump()}],
                       start_at=NOW + timedelta(seconds=10))
        result = planner.plan(spec, GAME, now=NOW)
        self.assertEqual(result["status"], "time_infeasible")
        self.assertEqual(result["stops"], [])

    def test_catalogue_objects_are_not_mutated(self):
        candidate = place("a", 50)
        original = dict(candidate)
        self.plan([candidate])
        self.assertEqual(candidate, original)

    def test_legacy_cuisine_and_named_exclusion_keep_semantics(self):
        def lookup(candidate, requirements, **kwargs):
            return answer(requirements, "pass" if candidate["name"] == "일식식당" else "fail"), {URL}, 1
        result = self.plan([place("한식", 20), place("싫은매장", 50, cuisine="일식"), place("일식식당", 650, cuisine="일식")],
                           lookup, spec=request(stops=[{"kind": "food", "cuisine": "일식", "excluded_keywords": ["싫은매장"]}]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["name"], "일식식당")

    def test_success_render_has_clickable_evidence_and_scope_disclaimer(self):
        result = self.plan([place("a", 50)])
        text = render_itinerary(result, food_request())
        self.assertIn("[음식 근거 1](<" + URL + ">)", text)
        self.assertIn("모델이 해석한 결과", text)
        self.assertIn("영업시간", text)

    @override_settings(COURSE_WEB_VERIFICATION_ENABLED=True)
    def test_service_wires_verification_and_saves_selected_evidence(self):
        inputs = {"question": "돈카츠 먹고 직관", "course_anchor": {
            "stadium_code": "JAMSIL", "stadium_name": "잠실", "starts_at": GAME.isoformat()}, "course_state": {}}
        data = {"places": [place("a", 50)], "coverage": COVERAGE, "snapshotId": "fixture"}
        verifier = FoodVerifier(lookup=provider, now=NOW)
        with patch("llm.v2.course.itinerary_service.extract_itinerary", return_value=food_request()), \
                patch("llm.v2.course.itinerary_service.load_planning_data", return_value=(data, STADIUM)), \
                patch("llm.v2.course.itinerary_service.KnowledgeFoodVerifier", return_value=verifier), \
                patch("llm.v2.course.itinerary_service.timezone.now", return_value=NOW), \
                patch("travel.directions_provider.fetch_directions", side_effect=route):
            text = "".join(generate_itinerary(inputs))
        self.assertIn("음식 근거", text)
        self.assertEqual(inputs["course_state"]["itinerary_result"]["food_search"]["lookups"], 1)
        self.assertEqual(inputs["course_state"]["itinerary_request"]["stops"][0]["food"], need().model_dump())
