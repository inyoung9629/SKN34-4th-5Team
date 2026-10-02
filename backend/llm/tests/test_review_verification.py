"""Offline review semantics, evidence, cost, timing, ranking and service regressions."""
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from llm.v2.course.internal_visits import choose_internal
from llm.v2.course.itinerary_planner import ItineraryPlanner
from llm.v2.course.itinerary_request import ItineraryRequest, StopRequest, extract_itinerary
from llm.v2.course.itinerary_service import generate_itinerary, render_itinerary
from llm.v2.course.review_requirements import ReviewRequirements
from llm.v2.course.review_verification import (
    ReviewObservation, ReviewSearchAnswer, ReviewSearchPolicy, ReviewVerifier,
    evaluate_reviews, search_reviews,
)
from .test_food_verification import FoodVerifier, need, provider as food_provider
from .test_itinerary_planner import COVERAGE, GAME, NOW, STADIUM, place, request, route

URL = "https://example.com/restaurant"


def requirements(priority="required", *aspects):
    return ReviewRequirements(all_of=[{"aspect": aspect, "priority": priority}
                                     for aspect in (aspects or ("quietness",))])


def observation(index=1, **updates):
    return {"url": f"https://reviews.example/post/{index}", "experience_key": f"visit-{index}",
            "same_branch": True, "body_read": True, "kind": "customer_review", "aspect": "quietness",
            "polarity": "positive", "promotion": "not_disclosed", "published_on": NOW.date(),
            "visited_on": None, "context": "general", "weekdays": [], "start_minute": None,
            "end_minute": None, "summary": f"가상 방문 {index}에서 소음이 적다는 경험", **updates}


def reply(observations=None, **updates):
    observations = [observation(1), observation(2)] if observations is None else observations
    urls = {URL, *(o["url"] for o in observations)}
    return ReviewSearchAnswer.model_validate({"identity": "match", "identity_urls": [URL],
        "scope": "external", "scope_urls": [URL],
        "sources": [{"url": u, "body_read": True, "summary": "가상의 해당 지점 출처"} for u in sorted(urls)],
        "observations": observations, **updates})


def provider(candidate, spec, **kwargs):
    data = reply()
    return data, {s.url for s in data.sources}, 1


def spec(priority="required", **extra):
    return request(stops=[{"kind": "food", "reviews": requirements(priority).model_dump()}], **extra)


class ReviewEvidenceTests(SimpleTestCase):
    def evaluate(self, data=None, *, required=None, urls=None, arrival=NOW, departure=None, calls=1):
        data = data or reply()
        lookup = Mock(return_value=(data, {s.url for s in data.sources} if urls is None else urls, calls))
        verifier = ReviewVerifier(lookup=lookup, now=NOW)
        required = required or requirements()
        report = verifier.verify(place("가상식당"), required)
        return evaluate_reviews(report, required, arrival=arrival, departure=departure or arrival + timedelta(hours=1))

    def test_two_independent_positive_reviews_pass(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["conditions"][0]["positive_count"], 2)
        self.assertEqual(result["checked_at"], NOW.isoformat())

    def test_one_positive_is_not_consensus(self):
        result = self.evaluate(reply([observation()]))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["conditions"][0]["positive_count"], 1)

    def test_missing_reviews_does_not_prove_negative(self):
        self.assertEqual(self.evaluate(reply([]))["conditions"][0]["status"], "unknown")

    def test_opposing_evidence_is_mixed_not_majority_vote(self):
        result = self.evaluate(reply([observation(1), observation(2), observation(3, polarity="negative")]))
        self.assertEqual(result["conditions"][0]["status"], "mixed")
        self.assertEqual(result["status"], "unknown")

    def test_only_negative_means_negative_evidence_not_actual_fact(self):
        result = self.evaluate(reply([observation(polarity="negative")]))
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["conditions"][0]["status"], "negative")

    def test_soft_unknown_or_negative_does_not_block(self):
        for data in (reply([]), reply([observation(polarity="negative")])):
            with self.subTest(data=data):
                result = self.evaluate(data, required=requirements("preferred"))
                self.assertTrue(result["eligible"])
                self.assertNotEqual(result["status"], "pass")
                self.assertLessEqual(result["preference_score"], 0)

    def test_required_pass_with_optional_unknown_is_eligible_not_fully_matched(self):
        need_both = ReviewRequirements(all_of=[{"aspect": "quietness", "priority": "required"},
                                              {"aspect": "cleanliness", "priority": "preferred"}])
        result = self.evaluate(required=need_both)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "review_unverified")

    def test_optional_negative_does_not_misreport_required_unknown_as_negative(self):
        need_both = ReviewRequirements(all_of=[{"aspect": "quietness", "priority": "required"},
                                              {"aspect": "cleanliness", "priority": "preferred"}])
        result = self.evaluate(reply([observation(aspect="cleanliness", polarity="negative")]), required=need_both)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "review_unverified")

    def test_taste_and_decor_are_not_cleanliness(self):
        for aspect in ("taste", "decor"):
            with self.subTest(aspect=aspect):
                data = reply([observation(1, aspect=aspect), observation(2, aspect=aspect)])
                result = self.evaluate(data, required=requirements("required", "cleanliness"))
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["observations"], [])

    def test_each_requested_aspect_needs_its_own_evidence(self):
        need_both = requirements("required", "quietness", "cleanliness")
        self.assertEqual(self.evaluate(required=need_both)["status"], "unknown")
        data = reply([observation(1), observation(2), observation(3, aspect="cleanliness"), observation(4, aspect="cleanliness")])
        self.assertEqual(self.evaluate(data, required=need_both)["status"], "pass")

    def test_same_page_tracking_url_repost_and_duplicate_summary_count_once(self):
        variants = [{"url": observation()["url"]}, {"url": observation()["url"] + "?utm_source=test#review"},
                    {"experience_key": observation()["experience_key"]}, {"summary": observation()["summary"]}]
        for update in variants:
            with self.subTest(update=update):
                result = self.evaluate(reply([observation(), observation(2, **update)]))
                self.assertEqual(result["conditions"][0]["positive_count"], 1)
                self.assertEqual(result["status"], "unknown")

    def test_conflicting_duplicate_is_not_hidden(self):
        result = self.evaluate(reply([observation(1), observation(2, experience_key="visit-1", polarity="negative")]))
        self.assertEqual(result["conditions"][0]["status"], "mixed")

    def test_unreliable_observations_cannot_pass(self):
        variants = [{"same_branch": False}, {"body_read": False}, {"promotion": "disclosed"},
                    {"promotion": "unknown"}, {"kind": "owner_promotion"}, {"kind": "platform_summary"},
                    {"published_on": None}, {"published_on": NOW.date() - timedelta(days=181)},
                    {"published_on": NOW.date() + timedelta(days=1)},
                    {"visited_on": NOW.date() - timedelta(days=181)}, {"context": "unknown"}]
        for update in variants:
            with self.subTest(update=update):
                result = self.evaluate(reply([observation(1, **update), observation(2, **update)]))
                self.assertEqual(result["status"], "unknown")

    def test_date_boundary_and_known_recent_visit_without_publication(self):
        for update in ({"published_on": NOW.date() - timedelta(days=180)},
                       {"published_on": None, "visited_on": NOW.date()}):
            with self.subTest(update=update):
                self.assertEqual(self.evaluate(reply([observation(1, **update), observation(2, **update)]))["status"], "pass")

    def test_visiting_after_publication_is_invalid(self):
        update = {"published_on": NOW.date() - timedelta(days=1), "visited_on": NOW.date()}
        self.assertEqual(self.evaluate(reply([observation(1, **update), observation(2, **update)]))["status"], "unknown")

    def test_model_only_urls_no_tools_or_wrong_branch_never_pass(self):
        for update in ({"identity": "mismatch"}, {"identity": "unknown"}, {"scope": "unknown"}, {"scope": "internal"}):
            with self.subTest(update=update):
                self.assertEqual(self.evaluate(reply(**update))["status"], "unknown")
        self.assertEqual(self.evaluate(urls=set())["status"], "unknown")
        self.assertEqual(self.evaluate(calls=0)["status"], "unknown")
        self.assertEqual(self.evaluate(calls=4)["status"], "unknown")

    def test_private_or_local_review_urls_never_count(self):
        data = reply([observation(1, url="http://127.0.0.1/a"), observation(2, url="http://169.254.169.254/b")])
        self.assertEqual(self.evaluate(data)["status"], "unknown")

    def test_snippet_only_identity_cannot_pass(self):
        data = reply()
        data.sources = [s.model_copy(update={"body_read": False}) for s in data.sources]
        self.assertEqual(self.evaluate(data)["status"], "unknown")

    def test_exact_time_window_covers_whole_stay(self):
        context = {"context": "limited", "weekdays": [NOW.weekday()], "start_minute": 720, "end_minute": 780}
        data = reply([observation(1, **context), observation(2, **context)])
        self.assertEqual(self.evaluate(data)["status"], "pass")
        self.assertEqual(self.evaluate(data, departure=NOW + timedelta(hours=1, seconds=1))["status"], "unknown")
        self.assertEqual(self.evaluate(data, arrival=NOW - timedelta(seconds=1))["status"], "unknown")

    def test_weekday_review_not_applied_to_weekend_or_next_day(self):
        context = {"context": "limited", "weekdays": [0, 1, 2, 3, 4]}
        data = reply([observation(1, **context), observation(2, **context)])
        self.assertEqual(self.evaluate(data, arrival=NOW + timedelta(days=2))["status"], "unknown")
        self.assertEqual(self.evaluate(data, arrival=NOW.replace(hour=23, minute=30))["status"], "unknown")

    def test_conditional_evidence_does_not_inflate_untimed_preference_rank(self):
        data = reply([observation(i, context="limited", weekdays=[NOW.weekday()]) for i in (1, 2)])
        verifier = ReviewVerifier(lookup=lambda *a, **kw: (data, {s.url for s in data.sources}, 1), now=NOW)
        need_soft = requirements("preferred")
        report = verifier.verify(place("a"), need_soft)
        self.assertEqual(evaluate_reviews(report, need_soft)["preference_score"], 0)
        self.assertEqual(evaluate_reviews(report, need_soft, arrival=NOW, departure=NOW + timedelta(hours=1))["preference_score"], 2)


class ReviewSchemaAndBudgetTests(SimpleTestCase):
    def test_duplicate_aspects_wrong_kind_and_invalid_time_ranges_rejected(self):
        with self.assertRaises(ValueError):
            requirements("required", "quietness", "quietness")
        with self.assertRaises(ValueError):
            StopRequest(kind="stay", reviews=requirements())
        for update in ({"context": "limited"}, {"weekdays": [7]}, {"start_minute": 720},
                       {"context": "limited", "start_minute": 900, "end_minute": 800}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                ReviewObservation.model_validate(observation(**update))

    def test_internal_reviews_preserved_but_not_supported(self):
        stop = StopRequest(kind="food", phase="inside", reviews=requirements())
        self.assertEqual(choose_internal([stop], [])[1], "internal_details_unverified")
        lookup = Mock()
        verifier = ReviewVerifier(lookup=lookup)
        verifier.verify(place("inside", stadiumAffiliation={"scope": "internal"}), requirements())
        lookup.assert_not_called()

    def test_cache_scoped_to_request_and_exact_conditions(self):
        lookup = Mock(side_effect=provider)
        verifier = ReviewVerifier(lookup=lookup, now=NOW)
        for condition in (requirements(), requirements(), requirements("preferred")):
            verifier.verify(place("a"), condition)
        self.assertEqual(lookup.call_count, 2)
        ReviewVerifier(lookup=lookup, now=NOW).verify(place("a"), requirements())
        self.assertEqual(lookup.call_count, 3)

    def test_exceptions_and_max_lookups_fail_safely(self):
        lookup = Mock(side_effect=TimeoutError("PRIVATE"))
        verifier = ReviewVerifier(lookup=lookup, now=NOW)
        for i in range(6):
            report = verifier.verify(place(str(i)), requirements())
        self.assertEqual(lookup.call_count, 4)
        self.assertEqual(report["reason"], "review_search_limited")
        self.assertNotIn("PRIVATE", str(verifier.audit()))

    def test_auth_error_stops_repeated_paid_calls(self):
        error = type("AuthenticationError", (Exception,), {})
        lookup = Mock(side_effect=error())
        verifier = ReviewVerifier(lookup=lookup, now=NOW)
        verifier.verify(place("a"), requirements())
        verifier.verify(place("b"), requirements())
        self.assertEqual(lookup.call_count, 1)

    def test_late_result_and_remaining_timeout(self):
        elapsed, timeouts = [0], []
        def slow(*args, **kwargs):
            timeouts.append(kwargs["timeout"])
            elapsed[0] += 25
            return provider(*args, **kwargs)
        verifier = ReviewVerifier(lookup=slow, now=NOW, clock=lambda: elapsed[0])
        for name in ("a", "b", "c", "d"):
            result = verifier.verify(place(name), requirements())
        self.assertEqual(timeouts, [25, 25, 10])
        self.assertEqual(result["reason"], "review_search_limited")

    def test_provider_privacy_model_bounds_and_sources(self):
        data = reply()
        response = SimpleNamespace(status="completed", output_parsed=data, model_dump=lambda **kw: {
            "output": [{"type": "web_search_call", "status": "completed", "action": {"sources": [{"url": URL}]}}]})
        client = Mock()
        client.responses.parse.return_value = response
        with patch("openai.OpenAI") as constructor, patch("llm.v2.agent.common.llm", return_value=SimpleNamespace(model_name="configured-model")):
            constructor.return_value.__enter__.return_value = client
            _, urls, calls = search_reviews({**place("식당"), "user_id": "PRIVATE", "history": "PRIVATE"},
                                            requirements(), timeout=9, max_tool_calls=3)
        args = client.responses.parse.call_args.kwargs
        self.assertEqual(args["model"], "configured-model")
        self.assertEqual(args["max_tool_calls"], 3)
        self.assertFalse(args["store"])
        self.assertNotIn("PRIVATE", args["input"])
        self.assertNotIn("lat", json.loads(args["input"])["restaurant"])
        self.assertEqual(constructor.call_args.kwargs, {"timeout": 9, "max_retries": 0})
        self.assertEqual((urls, calls), ({URL}, 1))

    def test_extraction_schema_and_previous_request_are_preserved(self):
        old = spec().model_dump(mode="json")
        answer = SimpleNamespace(content=json.dumps(old))
        model = Mock()
        model.invoke.return_value = answer
        with patch("llm.v2.agent.common.llm", return_value=model):
            result = extract_itinerary("그 조건 유지", {}, {}, previous=old, now=NOW)
        self.assertEqual(result.stops[0].reviews, requirements())
        messages = model.invoke.call_args.args[0]
        self.assertEqual(ItineraryRequest.model_validate(json.loads(messages[1].content)["previous_request"]).model_dump(mode="json"), old)
        self.assertIn("국물 맛이 깔끔한", messages[0].content)


class ReviewPlannerTests(SimpleTestCase):
    def plan(self, candidates=None, *, data=None, need_spec=None, lookup=None, **kwargs):
        data = data or reply()
        verifier = ReviewVerifier(lookup=lookup or (lambda *a, **kw: (data, {s.url for s in data.sources}, 1)), now=NOW)
        planner = ItineraryPlanner(candidates or [place("a", 30)], STADIUM, COVERAGE, route,
                                   review_verifier=verifier, **kwargs)
        return planner.plan(need_spec or spec(), GAME, now=NOW)

    def test_review_does_not_change_coordinates_or_bypass_timing(self):
        candidate = place("a", 30)
        copy = dict(candidate)
        result = self.plan([candidate])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["lat"], candidate["lat"])
        self.assertEqual(candidate, copy)
        self.assertEqual(self.plan(need_spec=spec(start_at=GAME.replace(hour=18)))["status"], "time_infeasible")

    def test_required_unknown_does_not_return_partial_course(self):
        result = self.plan(data=reply([]))
        self.assertEqual(result["status"], "review_unverified")
        self.assertEqual(result["stops"], [])

    def test_required_negative_is_not_reported_as_missing_data(self):
        result = self.plan(data=reply([observation(polarity="negative")]))
        self.assertEqual(result["status"], "review_mismatch")

    def test_missing_verifier_required_blocks_preferred_discloses(self):
        for priority, expected in (("required", "review_search_unavailable"), ("preferred", "ok")):
            planner = ItineraryPlanner([place("a", 30)], STADIUM, COVERAGE, route)
            result = planner.plan(spec(priority), GAME, now=NOW)
            self.assertEqual(result["status"], expected)
            if priority == "preferred":
                self.assertIn("확인하지 못했어요", render_itinerary(result, spec(priority)))

    def test_expands_for_required_reviews(self):
        def lookup(candidate, need_spec, **kwargs):
            data = reply([]) if candidate["name"] == "near" else reply()
            return data, {s.url for s in data.sources}, 1
        result = self.plan([place("near", 30), place("far", 220)], lookup=lookup)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["name"], "far")
        self.assertEqual(result["stops"][0]["search_radius_m"], 300)

    def test_preferred_general_reviews_rank_within_small_same_ring_batch(self):
        def lookup(candidate, need_spec, **kwargs):
            data = reply([]) if candidate["name"] == "near" else reply()
            return data, {s.url for s in data.sources}, 1
        result = self.plan([place("near", 20), place("preferred", 60)], lookup=lookup, need_spec=spec("preferred"))
        self.assertEqual(result["stops"][0]["name"], "preferred")
        self.assertEqual(result["review_search"]["lookups"], 2)

    def test_soft_unknown_still_returns_course_with_honest_label(self):
        result = self.plan(data=reply([]), need_spec=spec("preferred"))
        self.assertEqual(result["status"], "ok")
        text = render_itinerary(result, spec("preferred"))
        self.assertIn("근거 부족/미확인", text)
        self.assertIn("[지점·검토 자료", text)
        self.assertNotIn("[후기 근거", text)

    def test_final_time_not_search_time_determines_context(self):
        context = {"context": "limited", "weekdays": [], "start_minute": 720, "end_minute": 780}
        data = reply([observation(1, **context), observation(2, **context)])
        # Search NOW is noon but the suggested meal is 17:00~18:00.
        self.assertEqual(self.plan(data=data)["status"], "review_unverified")
        # A real clock tick would make exact NOW a past fixed start; use a frozen clock.
        result = self.plan(data=data, need_spec=spec(start_at=NOW), clock=lambda: 0)
        self.assertEqual(result["status"], "ok")

    def test_backtracking_reassesses_time_but_reuses_evidence(self):
        cafes = [place("slow", 20, "cafe"), place("fast", 40, "cafe")]
        food = place("food", 60)
        context = {"context": "limited", "weekdays": [], "start_minute": 970, "end_minute": 1030}
        data = reply([observation(1, **context), observation(2, **context)])
        lookup = Mock(return_value=(data, {s.url for s in data.sources}, 1))
        verifier = ReviewVerifier(lookup=lookup, now=NOW)
        def directions(mode, points):
            result = route(mode, points)
            if points[0]["lat"] == cafes[0]["lat"]:
                result["seconds"] = result["legs"][0]["seconds"] = 600
            return result
        planner = ItineraryPlanner([*cafes, food], STADIUM, COVERAGE, directions, review_verifier=verifier)
        req = request(stops=[{"kind": "cafe"}, {"kind": "food", "reviews": requirements().model_dump()}],
                      start_at=NOW.replace(hour=15, minute=20))
        result = planner.plan(req, GAME, now=NOW)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["name"], "fast")
        self.assertEqual(result["stops"][1]["arrive_at"], NOW.replace(hour=16, minute=10).isoformat())
        self.assertEqual(lookup.call_count, 1)

    def test_late_after_search_rechecks_now_visit_review_window(self):
        elapsed = [0.0]
        context = {"context": "limited", "weekdays": [], "start_minute": 720, "end_minute": 781}
        def lookup(candidate, condition, **kwargs):
            elapsed[0] += 25
            data = reply([observation(1, **context), observation(2, **context)]) if candidate["name"] == "before" else reply()
            return data, {s.url for s in data.sources}, 1
        verifier = ReviewVerifier(lookup=lookup, now=NOW, clock=lambda: elapsed[0])
        req = request(stops=[{"kind": "food", "required_keywords": ["before"], "reviews": requirements().model_dump()},
                             {"kind": "food", "phase": "after", "required_keywords": ["after"], "reviews": requirements().model_dump()}],
                      start_now=True)
        planner = ItineraryPlanner([place("before", 30), place("after", 50)], STADIUM, COVERAGE, route,
                                   review_verifier=verifier, clock=lambda: elapsed[0])
        result = planner.plan(req, GAME, now=NOW)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["start_at"], (NOW + timedelta(seconds=50)).isoformat())
        self.assertEqual(result["stops"][0]["review_verification"]["evaluated_arrival"], result["start_at"])

    def test_food_and_reviews_are_both_required(self):
        combined = request(stops=[{"kind": "food", "food": need().model_dump(), "reviews": requirements().model_dump()}])
        result = self.plan(need_spec=combined, food_verifier=FoodVerifier(lookup=food_provider, now=NOW))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stops"][0]["food_verification"]["status"], "pass")
        self.assertEqual(result["stops"][0]["review_verification"]["status"], "pass")
        self.assertEqual(self.plan(need_spec=combined)["status"], "food_search_unavailable")

    def test_unrelated_hard_conditions_still_block_before_search(self):
        lookup = Mock(side_effect=provider)
        result = self.plan(lookup=lookup, need_spec=spec(unverified_requirements=["알레르기 안전 보장"]))
        self.assertEqual(result["status"], "constraints_unverified")
        lookup.assert_not_called()

    def test_limited_search_is_not_no_restaurant(self):
        result = self.plan([place(str(i), i * 10) for i in range(6)], data=reply([]))
        self.assertEqual(result["status"], "review_search_limited")
        self.assertEqual(result["review_search"]["lookups"], 4)

    @override_settings(COURSE_WEB_VERIFICATION_ENABLED=True)
    def test_render_and_service_keep_evidence_in_existing_room_state(self):
        inputs = {"question": "조용한 식당", "course_anchor": {"stadium_code": "JAMSIL", "stadium_name": "잠실",
                  "starts_at": GAME.isoformat()}, "course_state": {}}
        data = {"places": [place("a", 30)], "coverage": COVERAGE, "snapshotId": "fixture"}
        with patch("llm.v2.course.itinerary_service.extract_itinerary", return_value=spec()), \
                patch("llm.v2.course.itinerary_service.load_planning_data", return_value=(data, STADIUM)), \
                patch("llm.v2.course.itinerary_service.KnowledgeReviewVerifier", return_value=ReviewVerifier(lookup=provider, now=NOW)), \
                patch("llm.v2.course.itinerary_service.timezone.now", return_value=NOW), \
                patch("travel.directions_provider.fetch_directions", side_effect=route):
            text = "".join(generate_itinerary(inputs))
        self.assertIn("[후기 근거", text)
        self.assertIn("위생 안전을 보장하지", text)
        state = inputs["course_state"]
        self.assertEqual(state["itinerary_request"]["stops"][0]["reviews"], requirements().model_dump())
        self.assertEqual(state["itinerary_result"]["review_search"]["lookups"], 1)
