"""Offline Ragas replay of saved real Specialist outputs; no provider or judge calls."""
import argparse
import json
import os
from pathlib import Path
import sys

os.environ["RAGAS_DO_NOT_TRACK"] = "true"
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["LANGSMITH_TRACING"] = "false"
from ragas import EvaluationDataset, RunConfig, SingleTurnSample, evaluate
from ragas.metrics.base import MetricType, SingleTurnMetric

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llm.highlight_deepeval import scores


class CorpusRagasMetric(SingleTurnMetric):
    def __init__(self, key):
        super().__init__(name=key, _required_columns={MetricType.SINGLE_TURN: {"response", "reference"}})
        self.key = key

    def init(self, run_config):
        pass  # Deterministic metric: no LLM/embedding dependency or initialization.

    async def _single_turn_ascore(self, sample, callbacks):
        score = scores(json.loads(sample.reference), json.loads(sample.response))[self.key]
        if score is None:
            raise ValueError("Exclude inconclusive/inapplicable rows before Ragas evaluation")
        return score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    records = json.loads(args.raw_rows.read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    all_scores, summary = {}, {}
    for key in ("recall_at_3", "final_selection", "original_time", "no_match"):
        applicable = [r for r in records if scores(r["label"], r["result"])[key] is not None]
        if not applicable:
            summary[key] = {"passed": 0, "denominator": 0, "rate": None}
            continue
        samples = [SingleTurnSample(user_input=r["query"], response=json.dumps(r["result"]),
                                    reference=json.dumps(r["label"]),
                                    retrieved_contexts=[r["result"]["candidate_rows"][sid]["text"] for sid in r["result"]["retrieval_only"]],
                                    retrieved_context_ids=r["result"]["retrieval_only"],
                                    reference_context_ids=[ref["segment_id"] for ref in r["label"]["references"]]) for r in applicable]
        result = evaluate(EvaluationDataset(samples=samples), metrics=[CorpusRagasMetric(key)],
                          run_config=RunConfig(max_workers=1, max_retries=0),
                          raise_exceptions=True, show_progress=False)
        values = [row[key] for row in result.scores]
        all_scores[key] = [{"id": r["id"], "score": v} for r, v in zip(applicable, values)]
        summary[key] = {"passed": sum(v == 1 for v in values), "denominator": len(values), "rate": sum(values) / len(values)}
    abstentions = [r for r in records if scores(r["label"], r["result"])["final_selection"] is not None and r["result"].get("no_match") is True]
    count = sum(not r["label"]["references"] for r in abstentions)
    summary["no_match_precision"] = {"passed": count, "denominator": len(abstentions), "rate": count / len(abstentions) if abstentions else None}
    summary.update({"framework": "ragas", "framework_version": __import__("importlib.metadata", fromlist=["version"]).version("ragas"),
                    "replayed_cases": len(records), "additional_provider_calls": 0, "judge_calls": 0,
                    "api_error_cases": sum(bool(r["api_errors"]) for r in records),
                    "inconclusive_cases": sum(scores(r["label"], r["result"])["final_selection"] is None for r in records),
                    "model_versions": sorted({d["model_version"] for r in records for d in r["result"].get("decisions", []) if d.get("model_version")})})
    (args.output / "ragas-results.json").write_text(json.dumps(all_scores, indent=2))
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
