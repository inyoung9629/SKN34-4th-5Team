"""Offline semantics check; no provider calls, requires isolated evaluation venv."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from llm.highlight_deepeval import CorpusMetric, scores
from deepeval.test_case import LLMTestCase


class EvaluationSemanticsTest(unittest.TestCase):
    def test_bound_match_abstention_and_inconclusive(self):
        label = {"references": [{"segment_id": "s", "video_id": "v", "original_interval": [10, 12]}]}
        row = {"id": "s", "video_id": "v", "start": 10, "end": 34}
        output = {"candidate_id": "s", "match": True, "local_seconds": 1, "evidence": "visible"}
        match = {"candidate_id": "s", "video_id": "v", "playback_seconds": 11.0, "evidence": "visible"}
        result = {"complete": True, "no_match": False, "retrieval_only": ["s"],
                  "decisions": [{"candidate_id": "s", "output": output}], "matches": [match],
                  "candidate_rows": {"s": row}}
        self.assertEqual(scores(label, result), {"recall_at_3": 1., "final_selection": 1., "original_time": 1., "no_match": None})
        metric = CorpusMetric("original_time")
        self.assertEqual(metric.measure(LLMTestCase(input="offline", expected_output=json.dumps(label), actual_output=json.dumps(result))), 1.)
        from llm.highlight_ragas import CorpusRagasMetric
        from ragas import SingleTurnSample
        self.assertEqual(CorpusRagasMetric("original_time").single_turn_score(
            SingleTurnSample(response=json.dumps(result), reference=json.dumps(label))), 1.)
        later = copy.deepcopy(result)
        later["decisions"][0]["output"]["local_seconds"] = 3
        later["matches"][0]["playback_seconds"] = 13
        self.assertEqual(scores(label, later)["original_time"], 0.)
        negative = {"references": []}
        self.assertEqual(scores(negative, result)["no_match"], 0.)
        abstain = copy.deepcopy(result)
        abstain["decisions"][0]["output"]["match"] = False
        abstain.update(matches=[], no_match=True)
        self.assertEqual(scores(negative, abstain)["no_match"], 1.)
        self.assertEqual(scores(label, abstain)["final_selection"], 0.)
        for bad in ({}, {"complete": False, "no_match": True, "api_errors": ["Google generateContent HTTP 429"]}):
            self.assertIsNone(scores(negative, bad)["no_match"])
        for patch in ({"local_seconds": float("nan")}, {"candidate_id": "other"}, {"match": "true"}, {"local_seconds": -1}):
            bad = copy.deepcopy(result)
            bad["decisions"][0]["output"].update(patch)
            self.assertIsNone(scores(label, bad)["final_selection"])
        for marker in ("provider_error", "rejected"):
            bad = copy.deepcopy(abstain)
            bad["decisions"][0][marker] = "HTTP 503" if marker == "provider_error" else "malformed"
            self.assertIsNone(scores(negative, bad)["no_match"])
        bad = copy.deepcopy(result)
        bad["matches"][0]["video_id"] = "forged"
        self.assertIsNone(scores(label, bad)["final_selection"])


if __name__ == "__main__":
    unittest.main()
