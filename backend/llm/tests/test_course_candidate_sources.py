from contextvars import ContextVar
from threading import Barrier, get_ident
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.service.chat_runs import Stopped
from llm.v1.rag.course import agent, place_quality


class CandidateSourcesTests(SimpleTestCase):
    def test_vectors_and_live_reads_overlap_without_moving_quality_off_request_thread(self):
        main, gate = get_ident(), Barrier(3)
        local = ContextVar("candidate_sources_test", default="request")
        searches = [("FOOD_OUT", "meal", 8), ("CAFE", "after", 4)]
        calls = []

        def vectors(vector, code, category, limit):
            self.assertNotEqual(get_ident(), main)
            self.assertEqual(local.get(), "request")
            gate.wait(3)
            calls.append((vector, code, category, limit))
            local.set("worker")
            return [{"category": category}]

        def live(*args, **kwargs):
            self.assertEqual(get_ident(), main)
            self.assertFalse(place_quality._FRESH.get())
            gate.wait(3)
            local.set("verified-on-main")
            return [{"source": "live"}], {"food": {"count": 1}}

        with patch.object(agent, "search_places", side_effect=vectors), \
                patch.object(agent, "_live_candidates", side_effect=live):
            rows, data, raw, timing = agent._candidate_sources(
                "JAMSIL", {}, "식당 카페", None, ["FOOD", "CAFE"], searches,
                {"meal": [1], "after": [2]}, 2, research=False)
        self.assertEqual(rows, [{"source": "live"}])
        self.assertEqual(data, {"food": {"count": 1}})
        self.assertEqual(raw, [[{"category": "FOOD_OUT"}], [{"category": "CAFE"}]])
        self.assertCountEqual(calls, [([1], "JAMSIL", "FOOD_OUT", 48), ([2], "JAMSIL", "CAFE", 24)])
        self.assertEqual(local.get(), "verified-on-main")
        self.assertTrue(all(timing[k] >= 0 for k in ("live_ms", "vector_ms", "vector_wait_ms")))

    def test_empty_sources_are_retained_and_no_vector_call_is_added(self):
        with patch.object(agent, "search_places") as search, \
                patch.object(agent, "_live_candidates", return_value=([], {})):
            rows, data, raw, _ = agent._candidate_sources("JAMSIL", {}, "산책", None,
                ["WALK"], [], {}, 1, research=True)
        self.assertEqual((rows, data, raw), ([], {}, []))
        search.assert_not_called()

    def test_failed_vector_source_does_not_silently_turn_into_empty_candidates(self):
        for error in (Stopped, RuntimeError):
            with self.subTest(error=error), patch.object(agent, "search_places", side_effect=error), \
                    patch.object(agent, "_live_candidates", return_value=([], {})):
                with self.assertRaises(error):
                    agent._candidate_sources("JAMSIL", {}, "카페", None, ["CAFE"],
                        [("CAFE", "after", 4)], {"after": [1]}, 1, research=False)

    def test_creation_model_copy_cannot_lower_other_requests_reasoning(self):
        base, copied = Mock(), Mock()
        base.model_copy.return_value = copied
        with patch.object(agent, "_llm", base):
            self.assertIs(agent.llm(reasoning_effort="low"), copied)
            self.assertIs(agent.llm(), base)
        base.model_copy.assert_called_once_with(update={"reasoning_effort": "low"})
