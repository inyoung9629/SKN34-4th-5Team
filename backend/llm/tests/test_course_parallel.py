from contextvars import ContextVar
from functools import partial
from threading import Barrier, Event, Lock, get_ident
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.v1 import progress
from llm.v1.rag.course import agent, parallel, place_quality, route_ranking, venue_policy
from llm.tests.test_course_route_ranking import ANCHOR, ORIGIN, NEAR, FAR, CAFE, GAME, PARK, provider, step


class ParallelReadsTests(SimpleTestCase):
    def test_results_keep_input_order_when_second_request_finishes_first(self):
        second_done = Event()
        def first():
            self.assertTrue(second_done.wait(3), "Independent requests must overlap")
            return "first"
        def second():
            second_done.set()
            return "second"
        self.assertEqual(parallel.reads([first, second]), ["first", "second"])

    def test_two_workers_and_connections_are_closed_for_every_job(self):
        gate, lock = Barrier(2), Lock()
        active, peak = 0, 0
        def job(index):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            if index < 2:
                gate.wait(3)
            with lock:
                active -= 1
            return index
        with patch.object(parallel.connections, "close_all") as close:
            self.assertEqual(parallel.reads(partial(job, i) for i in range(5)), list(range(5)))
        self.assertEqual(peak, 2)
        self.assertEqual(close.call_count, 5)

    def test_worker_context_preserves_policy_and_does_not_leak_to_parent(self):
        local = ContextVar("parallel_test_local", default="outside")
        collector = SimpleNamespace(cancelled=False)
        token = progress._CURRENT.set(collector)
        self.addCleanup(progress._CURRENT.reset, token)
        def job():
            self.assertIs(progress.current(), collector)
            self.assertEqual(place_quality._SCOPE.get(), "GWANGJU")
            self.assertFalse(place_quality._FRESH.get())
            self.assertEqual(venue_policy._PERMISSION.get(), ("GWANGJU", frozenset({"CAFE"})))
            self.assertEqual(local.get(), "outside")
            local.set("worker")
            return "ok"
        with place_quality.request_scope("GWANGJU"), place_quality.research_policy(False), \
                venue_policy.request_policy("구장 안 카페", "GWANGJU", [{"category": "CAFE", "expression": "구장 안 카페"}]):
            self.assertEqual(parallel.reads([job, job]), ["ok", "ok"])
        self.assertEqual(local.get(), "outside")
        self.assertEqual(venue_policy._PERMISSION.get(), (None, frozenset()))

    def test_cancelled_request_starts_no_provider_work(self):
        token = progress._CURRENT.set(SimpleNamespace(cancelled=True))
        self.addCleanup(progress._CURRENT.reset, token)
        for count in (1, 2):
            job = Mock()
            with self.subTest(count=count), self.assertRaises(progress.ProgressCancelled):
                parallel.reads([job] * count)
            job.assert_not_called()

    def test_error_propagates_and_other_started_work_finishes_before_return(self):
        for error in (progress.ProgressCancelled, progress.ProgressStorageError, RuntimeError):
            gate, finished = Barrier(2), Event()
            def fail():
                gate.wait(3)
                raise error("test")
            def finish():
                gate.wait(3)
                finished.set()
            with self.subTest(error=error), self.assertRaises(error):
                parallel.reads([fail, finish])
            self.assertTrue(finished.is_set())


class ParallelDiscoveryTests(SimpleTestCase):
    def test_discovery_overlaps_but_quality_checks_and_output_stay_ordered(self):
        main = get_ident()
        gate = Barrier(2)
        anchor = {"lat": 35.2226, "lng": 128.5824}
        checked = []
        def invoke(domain, name, args):
            self.assertNotEqual(get_ident(), main)
            self.assertEqual(name, "search_places")
            gate.wait(3)
            return {"places": [{"id": args["category"], "place_name": args["category"],
                    "category_name": "카페" if args["category"] == "CE7" else "한식",
                    "y": "35.224", "x": "128.583", "road_address_name": "삼호로 70"}]}
        def verify(rows, category, *a, **kw):
            self.assertEqual(get_ident(), main, "Quality budgets must stay on the requesting thread")
            checked.append(category)
            return rows
        with patch.object(agent, "invoke_domain_tool", side_effect=invoke), \
                patch.object(place_quality, "verify_candidates", side_effect=verify):
            candidates, _ = agent._live_candidates("CHANGWON", anchor, "식사와 카페", None, kinds=["FOOD", "CAFE"])
        self.assertEqual(checked, ["FD6", "CE7"])
        self.assertEqual([p["category"] for p in candidates], ["FOOD_OUT", "CAFE"])

    def test_internal_cafe_stays_collected_only_during_parallel_food_lookup(self):
        from baseball.stadium_locations import reviewed_venue
        question = "식사하고 구장 안 카페"
        invoke = Mock(return_value={"places": []})
        with venue_policy.request_policy(question, "JAMSIL", [{"category": "CAFE", "expression": "구장 안 카페"}]), \
                patch.object(agent, "invoke_domain_tool", invoke):
            candidates, data = agent._live_candidates("JAMSIL", reviewed_venue("JAMSIL"), question, None, kinds=["FOOD", "CAFE"])
        self.assertTrue(candidates)
        self.assertEqual(data["cafe"]["source"], "MYSEATCHECK")
        self.assertTrue(all("myseatcheck.com" in p["placeUrl"] for p in candidates))
        self.assertEqual([call.args[2]["category"] for call in invoke.call_args_list], ["FD6"])


class ParallelRouteTests(SimpleTestCase):
    def test_two_failed_edges_in_one_route_do_not_hide_valid_alternative(self):
        inner = provider({route_ranking.edge_key(ORIGIN, NEAR): None,
                          route_ranking.edge_key(NEAR, CAFE): None})
        ranker = route_ranking.RouteRanker(inner)
        rows = ranker.optimize([step(NEAR), step(CAFE), GAME, step(PARK, "AFTER")],
            [[NEAR, FAR], [CAFE], [None], [PARK]], ORIGIN, ANCHOR, lambda p: 0)
        self.assertEqual(rows[0]["place"], FAR)
        self.assertEqual(ranker.report["measured"], 1)
        self.assertEqual(ranker.report["strategy"], "duration")

    def test_independent_edges_overlap_without_changing_route_or_call_count(self):
        gate, inner = Barrier(2), provider()
        main = get_ident()
        def invoke(*args):
            self.assertNotEqual(get_ident(), main)
            gate.wait(3)
            return inner(*args)
        ranker = route_ranking.RouteRanker(invoke)
        rows = ranker.optimize([step(NEAR), step(CAFE), GAME, step(PARK, "AFTER")],
            [[NEAR, FAR], [CAFE], [None], [PARK]], ORIGIN, ANCHOR, lambda p: 0)
        self.assertEqual(rows[0]["place"], NEAR)
        self.assertEqual(ranker.calls, 6)
        self.assertEqual(ranker.report["measured"], 2)
        self.assertTrue(all(ranker.known_legs(route_ranking.points_of(rows, ORIGIN, ANCHOR))))

    def test_storage_errors_are_not_changed_into_distance_estimates(self):
        ranker = route_ranking.RouteRanker(Mock(side_effect=progress.ProgressStorageError("test")))
        with self.assertRaises(progress.ProgressStorageError):
            ranker.optimize([step(NEAR), GAME], [[NEAR, FAR], [None]], ORIGIN, ANCHOR, lambda p: 0)
