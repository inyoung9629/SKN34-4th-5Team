"""Paid Ragas 0.4.3 scoring; independent of Django and its runtime dependencies."""
import argparse
import asyncio
import json
import math
import os
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

METRICS = ("context_precision", "context_recall", "faithfulness", "answer_relevancy")


def load_samples(text, limit):
    if limit <= 0:
        raise ValueError("limit must be positive")
    rows, seen = [], set()
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ("id", "user_input", "reference", "response")):
            raise ValueError("Each sample needs nonempty id/user_input/reference/response strings")
        if row["id"] in seen:
            raise ValueError("Duplicate ID")
        seen.add(row["id"])
        contexts = row.get("retrieved_contexts")
        if not isinstance(contexts, list) or not contexts or any(not isinstance(c, str) or not c.strip() for c in contexts):
            raise ValueError("retrieved_contexts must be a nonempty string list")
        if "metadata" in row and not isinstance(row["metadata"], dict):
            raise ValueError("metadata must be an object")
        for key in ("reference_doc_ids", "retrieved_doc_ids"):
            if key in row and (not isinstance(row[key], list) or any(not isinstance(v, str) for v in row[key])):
                raise ValueError("document IDs must be string lists")
        metadata = row.get("metadata", {})
        for key in ("dataset", "mode", "generation_model", "embedding_model", "scope"):
            if key in metadata and not isinstance(metadata[key], str):
                raise ValueError("metadata labels must be strings")
        if "k" in metadata and (type(metadata["k"]) is not int or not 1 <= metadata["k"] <= 50):
            raise ValueError("metadata k must be 1..50")
        if row.get("errors"):
            raise ValueError("Export contains errors")
        rows.append(row)
    if not rows:
        raise ValueError("Empty dataset")
    return rows[:limit]


def load_ragas():
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    try:
        from ragas.llms import llm_factory
        from ragas.embeddings.base import embedding_factory
        from ragas.metrics.collections import ContextPrecision, ContextRecall, Faithfulness, AnswerRelevancy
        if version("ragas") != "0.4.3":
            raise ImportError("Wrong Ragas version")
    except ImportError:
        raise ValueError("Install rag_test/requirements-ragas.txt in a separate evaluation venv (Ragas 0.4.3)") from None
    return llm_factory, embedding_factory, (ContextPrecision, ContextRecall, Faithfulness, AnswerRelevancy)


async def score_samples(rows, metrics):
    results = []
    for row in rows:
        # Only allow known fields; never serialize client objects, exceptions or headers.
        result = {k: row.get(k) for k in ("id", "question", "user_input", "reference", "reference_doc_ids", "response", "retrieved_contexts", "retrieved_doc_ids")}
        result.update(scores=dict.fromkeys(METRICS), errors={}, usage="unknown")
        inputs = (
            {"user_input": row["user_input"], "reference": row["reference"], "retrieved_contexts": row["retrieved_contexts"]},
            {"user_input": row["user_input"], "reference": row["reference"], "retrieved_contexts": row["retrieved_contexts"]},
            {"user_input": row["user_input"], "response": row["response"], "retrieved_contexts": row["retrieved_contexts"]},
            {"user_input": row["user_input"], "response": row["response"]},
        )
        for name, metric, arguments in zip(METRICS, metrics, inputs):
            try:
                scored = await asyncio.wait_for(metric.ascore(**arguments), timeout=120)
                value = float(scored.value)
                if not math.isfinite(value) or not (-1 if name == "answer_relevancy" else 0) <= value <= 1:
                    raise ValueError("Nonfinite or out-of-range score")
                result["scores"][name] = value
            except Exception as exc:
                result["errors"][name] = type(exc).__name__
        results.append(result)
    return results


async def evaluate(rows, model, factories):
    from openai import AsyncOpenAI
    llm_factory, embedding_factory, classes = factories
    async with AsyncOpenAI(timeout=30, max_retries=0) as client:
        llm = llm_factory(model, client=client, temperature=0, max_tokens=1800, max_retries=1)
        embeddings = embedding_factory("openai", model="text-embedding-3-small", client=client)
        metrics = [classes[0](llm=llm), classes[1](llm=llm), classes[2](llm=llm), classes[3](llm=llm, embeddings=embeddings)]
        return await score_samples(rows, metrics)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paid: score exported single-generation samples, not full chatbot E2E")
    parser.add_argument("--input", default="-")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--evaluation-model", default="gpt-4.1-mini")
    parser.add_argument("--output")
    o = parser.parse_args(argv)
    try:
        if not o.evaluation_model.strip():
            raise ValueError("evaluation model cannot be empty")
        text = sys.stdin.read() if o.input == "-" else Path(o.input).read_text(encoding="utf-8")
        rows = load_samples(text, o.limit)
        factories = load_ragas()
        output = Path(o.output) if o.output else Path("artifacts/rag_eval") / f"ragas_{datetime.now(timezone.utc):%Y%m%d_%H%M%S_%f}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            report = {"ragas_version": "0.4.3", "evaluation_model": o.evaluation_model, "embedding_model": "text-embedding-3-small", "dataset": o.input, "export_metadata": [{k: (r.get("metadata") or {}).get(k) for k in ("dataset", "mode", "k", "generation_model", "embedding_model", "scope")} for r in rows], "usage": "unknown (Ragas MetricResult exposes value/reason/traces, not provider token usage)", "results": [], "errors": {}}
            print("Paid evaluation API calls; RAGAS_DO_NOT_TRACK=true disables Ragas telemetry", file=sys.stderr)
            try:
                report["results"] = asyncio.run(evaluate(rows, o.evaluation_model, factories))
            except Exception as exc:
                report["errors"]["evaluation"] = type(exc).__name__
            successful = sum(not r["errors"] for r in report["results"])
            report["summary"] = {"requested": len(rows), "successful": successful, "failed": len(rows) - successful, "mean_scores": {name: (sum(values) / len(values) if values else None) for name in METRICS for values in [[r["scores"][name] for r in report["results"] if r["scores"][name] is not None]]}}
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(f"Results: {output}")
        return 0 if successful == len(rows) else 1
    except (ValueError, OSError) as exc:
        # Validation errors are ours; OSError can contain private paths so report type only.
        print(str(exc) if isinstance(exc, ValueError) else type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
