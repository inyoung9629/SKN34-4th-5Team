"""Export real retrieval + one generation for an isolated Ragas environment."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def select_questions(text, ids, limit):
    questions, seen, skipped = [], set(), []
    selected = [v.strip() for v in ids.split(",")] if ids else []
    if selected and (not all(selected) or len(set(selected)) != len(selected)):
        raise CommandError("--ids must contain unique nonempty IDs")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise CommandError("Invalid JSONL") from None
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ("id", "question")):
            raise CommandError("Each row needs nonempty string id/question")
        if row["id"] in seen:
            raise CommandError("Duplicate dataset ID")
        seen.add(row["id"])
        if "reference" not in row:
            skipped.append(row["id"])
            continue
        if not isinstance(row["reference"], str) or not row["reference"].strip():
            raise CommandError("reference must be a nonempty answer sentence")
        doc_ids = row.get("reference_doc_ids")
        if not isinstance(doc_ids, list) or not doc_ids or any(not isinstance(v, str) or not v.strip() for v in doc_ids):
            raise CommandError("reference_doc_ids must be a nonempty string list")
        if not selected or row["id"] in selected:
            questions.append(row)
    if selected and set(selected) - {q["id"] for q in questions}:
        raise CommandError("Selected IDs missing or without reference")
    if selected and len(questions) > limit:
        raise CommandError("--limit cannot omit explicitly selected IDs")
    if not questions:
        raise CommandError("No reference-bearing questions")
    return questions[:limit], skipped


class Command(BaseCommand):
    help = "Paid: export search + a single generation (not full chatbot E2E) for Ragas"

    def add_arguments(self, parser):
        parser.add_argument("--golden", default="-")
        parser.add_argument("--ids", default="")
        parser.add_argument("--limit", type=int, default=3)
        parser.add_argument("--k", "-k", type=int, default=5)
        parser.add_argument("--mode", default="rerank")
        parser.add_argument("--generation-model", default="gpt-4.1-mini")
        parser.add_argument("--output")

    def handle(self, *args, **o):
        from llm.tools import knowledge
        if o["limit"] <= 0 or not 1 <= o["k"] <= 50 or o["mode"] not in knowledge.SEARCH_MODES:
            raise CommandError("Require limit > 0, k 1..50 and a knowledge.SEARCH_MODES mode")
        if not o["generation_model"].strip():
            raise CommandError("generation model cannot be empty")
        try:
            text = sys.stdin.read() if o["golden"] == "-" else Path(o["golden"]).read_text(encoding="utf-8")
        except OSError:
            raise CommandError("Cannot read golden file") from None
        questions, skipped = select_questions(text, o["ids"], o["limit"])
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from llm.v1.rag.club import retrieval
        from llm.v1.rag.club.router import detect_categories, detect_stadium
        from llm.tools.assistant import format_documents
        from llm.v1.rag.assistant.pipeline import build_prompt
        if retrieval.EMBED_MODEL != "text-embedding-3-small":
            raise CommandError("This evaluation requires the existing corpus text-embedding-3-small model")
        output = Path(o["output"]) if o["output"] else Path(settings.BASE_DIR) / "artifacts/rag_eval" / f"ragas_export_{datetime.now(timezone.utc):%Y%m%d_%H%M%S_%f}.jsonl"
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            stream = output.open("x", encoding="utf-8")
        except OSError:
            raise CommandError("Output unavailable or already exists; choose a new path") from None
        self.stdout.write(f"Paid API calls: retrieval embeddings/rerank + one generation per question; skipped reference-less IDs ({len(skipped)}): {','.join(skipped)}")
        failed = False
        metadata = {"dataset": o["golden"], "mode": o["mode"], "k": o["k"], "generation_model": o["generation_model"], "embedding_model": retrieval.EMBED_MODEL, "scope": "retrieval_single_generation", "skipped_ids": skipped}
        try:
            model = ChatOpenAI(model=o["generation_model"], timeout=30, max_retries=0, max_tokens=1200)
            embedder = OpenAIEmbeddings(model=retrieval.EMBED_MODEL, dimensions=1536, request_timeout=30, max_retries=0)
            with stream, patch.object(retrieval, "_embedder", embedder):
                for q in questions:
                    result = {"id": q["id"], "question": q["question"], "user_input": q["question"], "reference": q["reference"], "reference_doc_ids": q["reference_doc_ids"], "response": None, "retrieved_contexts": [], "retrieved_doc_ids": [], "metadata": metadata, "usage": {"generation": "unknown", "retrieval": "unknown"}, "errors": {}}
                    try:
                        stadium = detect_stadium(q["question"])
                        rows = knowledge.search_kbo_rows(q["question"], stadium, detect_categories(q["question"]) or None, k=o["k"], mode=o["mode"])
                        # Keep each formatted document byte-identical in generation and scoring.
                        contexts = [format_documents([r]).replace("[1]", f"[{i}]", 1) for i, r in enumerate(rows, 1)]
                        result["retrieved_contexts"] = contexts
                        result["retrieved_doc_ids"] = [r.get("doc_id") for r in rows]
                        if not contexts:
                            raise ValueError("No contexts")
                        messages = build_prompt({"question": q["question"], "context": "\n\n".join(contexts), "hint": stadium, "history": []})["messages"]
                        response = model.invoke(messages)
                        if not isinstance(response.content, str) or not response.content.strip():
                            raise ValueError("Empty generation")
                        result["response"] = response.content
                        usage = getattr(response, "usage_metadata", None) or {}
                        result["usage"]["generation"] = {key: usage[key] for key in ("input_tokens", "output_tokens", "total_tokens") if isinstance(usage.get(key), int)} or "unknown"
                    except Exception as exc:
                        failed = True
                        result["errors"]["export"] = type(exc).__name__
                    stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
        except Exception as exc:
            stream.close()
            raise CommandError(f"Export setup failed: {type(exc).__name__}; partial output: {output}") from None
        self.stdout.write(f"Export: {output} ({len(questions)} rows)")
        if failed:
            raise CommandError("Export contains failures; partial results preserved")
