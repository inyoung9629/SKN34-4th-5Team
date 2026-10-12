"""Opt-in deterministic DeepEval of the real multimodal Specialist; no LLM judge."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

# Disable evaluation telemetry before importing the framework; never upload results.
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
from deepeval import evaluate
from deepeval.evaluate.configs import AsyncConfig, CacheConfig, DisplayConfig
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase


def scores(label, result):
    """None is inapplicable/inconclusive, never a successful abstention."""
    empty = dict.fromkeys(("recall_at_3", "final_selection", "original_time", "no_match"))
    try:
        refs = label["references"]
        candidates = result["retrieval_only"]
        if not isinstance(candidates, list) or len(candidates) > 3 or not all(isinstance(s, str) for s in candidates):
            return empty
        expected = {(r["video_id"], r["segment_id"]) for r in refs}
        empty["recall_at_3"] = float(any(sid in candidates for _, sid in expected)) if refs else None
        decisions = result["decisions"]
        if (result.get("complete") is not True or not isinstance(decisions, list)
                or [d.get("candidate_id") for d in decisions] != candidates
                or any("provider_error" in d or "rejected" in d for d in decisions)):
            return empty
        from llm.highlight_multimodal import decision
        validated = []
        for d in decisions:
            sid = d["candidate_id"]
            row = result["candidate_rows"][sid]
            if row["id"] != sid:
                return empty
            accepted = decision(d["output"], row)
            if accepted:
                validated.append(accepted)
        if result["matches"] != validated or result["no_match"] is not (not validated):
            return empty
        if not refs:
            empty["no_match"] = float(not validated)
            empty["final_selection"] = float(not validated)
            return empty
        correct = bool(validated) and all((m["video_id"], m["candidate_id"]) in expected for m in validated)
        empty["final_selection"] = float(correct)
        empty["original_time"] = float(correct and all(any(
            m["video_id"] == r["video_id"] and m["candidate_id"] == r["segment_id"]
            and r["original_interval"][0] <= m["playback_seconds"] <= r["original_interval"][1]
            for r in refs) for m in validated))
    except (KeyError, TypeError, ValueError, AttributeError):
        return empty
    return empty


class CorpusMetric(BaseMetric):
    def __init__(self, key):
        self.key = key
        self.threshold = 1.0
        self.async_mode = False
        self.strict_mode = True
        self.evaluation_model = "deterministic operator-label comparison (no judge)"

    @property
    def __name__(self):
        return self.key

    def measure(self, test_case, *args, **kwargs):
        self.score = scores(json.loads(test_case.expected_output), json.loads(test_case.actual_output))[self.key]
        if self.score is None:
            raise ValueError("Inconclusive/inapplicable case must be excluded before evaluation")
        self.success = self.score >= self.threshold
        self.reason = "Compared bound segment/video and predeclared interval; no semantic judge"
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def snapshot(source, target):
    # Same advisory lock as preview; do not open the live local Qdrant client.
    with (source / "web.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = hashes(source)
        shutil.copytree(source, target)
        if hashes(source) != before or hashes(target) != before:
            raise ValueError("Corpus changed during snapshot")
    return before


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New empty run directory")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--run-provider", action="store_true", help="Authorize one sequential finite pass")
    args = parser.parse_args()
    if not args.run_provider:
        parser.error("Provider evaluation requires explicit --run-provider")
    args.output.mkdir(parents=True, exist_ok=False)
    dataset = json.loads(args.dataset.read_text())
    if not 8 <= len(dataset["cases"]) <= 10:
        raise ValueError("Sample must contain 8–10 cases")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from llm.highlight_multimodal import Google, Specialist
    corpus_hashes = snapshot(args.corpus, args.output / "corpus-copy")
    (args.output / "corpus-hashes.json").write_text(json.dumps(corpus_hashes, indent=2))
    rows = json.loads((args.output / "corpus-copy" / "segments.json").read_text())
    if len(rows) != dataset["corpus_segments"]:
        raise ValueError("Unexpected corpus size")
    for case in dataset["cases"]:
        for ref in case["references"]:
            row = rows[ref["segment_id"]]
            lo, hi = ref["original_interval"]
            if row["video_id"] != ref["video_id"] or not row["start"] <= lo <= hi <= row["end"]:
                raise ValueError("Reference outside indexed segment")
    if args.env_file:
        from dotenv import dotenv_values
        key = dotenv_values(args.env_file).get("GOOGLE_API_KEY")
    else:
        key = os.environ.get("GOOGLE_API_KEY")
    google = Google(key=key, model="gemini-3.8-flash")
    specialist = Specialist(args.output / "corpus-copy", google)
    records, pressure_errors, halted = [], 0, False
    try:
        for case in dataset["cases"]:
            started = time.monotonic()
            try:
                result = specialist.ask(case["query"], candidate_limit=3)
                result["candidate_rows"] = {sid: rows[sid] for sid in result["retrieval_only"]}
                errors = [d["provider_error"] for d in result["decisions"] if "provider_error" in d]
            except RuntimeError as exc:
                # Shared transport sanitizes provider errors; do not persist arbitrary exceptions.
                errors = [str(exc)]
                result = {"complete": False, "no_match": None, "api_errors": errors}
            score = scores(case, result)
            records.append({"id": case["id"], "query": case["query"], "label": case,
                            "result": result, "scores": score, "api_errors": errors,
                            "latency_seconds": time.monotonic() - started})
            (args.output / "raw-rows.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
            print(case["id"], json.dumps(score), "api_errors=", len(errors), flush=True)
            pressure_errors += sum(bool(re.search(r"HTTP (429|503)\b", e)) for e in errors)
            if pressure_errors >= 2:
                halted = True
                break
            time.sleep(1)
    finally:
        specialist.close()
    framework = {}
    summary = {}
    for metric in ("recall_at_3", "final_selection", "original_time", "no_match"):
        applicable = [r for r in records if r["scores"][metric] is not None]
        summary[metric] = {"passed": sum(r["scores"][metric] == 1 for r in applicable),
                           "denominator": len(applicable),
                           "rate": sum(r["scores"][metric] for r in applicable) / len(applicable) if applicable else None}
        if applicable:
            evaluated = evaluate(
                [LLMTestCase(name=r["id"], input=r["query"], actual_output=json.dumps(r["result"]),
                             expected_output=json.dumps(r["label"])) for r in applicable],
                metrics=[CorpusMetric(metric)], async_config=AsyncConfig(run_async=False),
                cache_config=CacheConfig(write_cache=False, use_cache=False),
                display_config=DisplayConfig(show_indicator=False, print_results=False, inspect_after_run=False))
            framework[metric] = evaluated.model_dump(mode="json")
    abstentions = [r for r in records if r["scores"]["final_selection"] is not None and r["result"].get("no_match") is True]
    true_abstentions = sum(not r["label"]["references"] for r in abstentions)
    summary["no_match_precision"] = {"passed": true_abstentions, "denominator": len(abstentions),
                                     "rate": true_abstentions / len(abstentions) if abstentions else None}
    summary.update({"attempted": len(records), "planned": len(dataset["cases"]), "halted_on_repeated_429_503": halted,
                    "api_error_cases": sum(bool(r["api_errors"]) for r in records),
                    "inconclusive_selection_cases": sum(r["scores"]["final_selection"] is None for r in records),
                    "model_versions": sorted({d["model_version"] for r in records for d in r["result"].get("decisions", []) if d.get("model_version")}),
                    "corpus_unchanged": hashes(args.corpus) == corpus_hashes,
                    "total_case_latency_seconds": sum(r["latency_seconds"] for r in records)})
    (args.output / "deepeval-results.json").write_text(json.dumps(framework, ensure_ascii=False, indent=2))
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
