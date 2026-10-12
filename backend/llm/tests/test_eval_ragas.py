"""Network-free export and isolated evaluator regressions."""
import asyncio
import importlib.util
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from llm.management.commands.export_ragas import select_questions

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("isolated_eval_ragas", ROOT / "rag_test/eval_ragas.py")
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)
QUESTION = {"id": "test", "question": "준플레이오프?", "reference": "3선승제입니다.", "reference_doc_ids": ["source"]}


class RagasTest(SimpleTestCase):
    def test_reference_validation_selection_and_skip(self):
        text = json.dumps({"id": "old", "question": "old?"}) + "\n" + json.dumps(QUESTION)
        rows, skipped = select_questions(text, "test", 3)
        self.assertEqual(rows, [QUESTION])
        self.assertEqual(skipped, ["old"])
        for bad in ("", "[]", json.dumps(QUESTION) + "\n" + json.dumps(QUESTION), json.dumps({**QUESTION, "reference": ""}), json.dumps({**QUESTION, "reference_doc_ids": "source"})):
            with self.subTest(bad=bad), self.assertRaises(CommandError):
                select_questions(bad, "", 3)
        for ids in ("old", "missing", "test,test"):
            with self.assertRaises(CommandError):
                select_questions(text, ids, 3)

    def test_export_context_mapping_no_gold_filter_and_usage(self):
        rows = [{"doc_id": "actual", "content": "3선승제 원문", "category": "RULE"}, {"doc_id": "other", "content": "다른 원문", "category": "RULE"}]
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdin", io.StringIO(json.dumps(QUESTION))), patch("llm.tools.knowledge.search_kbo_rows", return_value=rows) as search, patch("langchain_openai.ChatOpenAI") as model, patch("langchain_openai.OpenAIEmbeddings"):
            model.return_value.invoke.return_value = SimpleNamespace(content="답변", usage_metadata={"input_tokens": 10, "output_tokens": 2, "secret": "not saved"})
            output = Path(directory) / "export.jsonl"
            call_command("export_ragas", output=str(output), stdout=io.StringIO())
            exported = json.loads(output.read_text())
            system = model.return_value.invoke.call_args.args[0][0].content
            self.assertIn("\n\n".join(exported["retrieved_contexts"]), system)
            self.assertEqual(exported["retrieved_doc_ids"], ["actual", "other"])
            self.assertNotIn("reference", search.call_args.kwargs)
            self.assertEqual(model.return_value.invoke.call_count, 1)
            self.assertNotIn("secret", output.read_text())
            with patch("sys.stdin", io.StringIO(json.dumps(QUESTION))), self.assertRaises(CommandError):
                call_command("export_ragas", output=str(output))
            self.assertEqual(search.call_count, 1)

    def test_invalid_parameters_before_search(self):
        with patch("llm.tools.knowledge.search_kbo_rows") as search:
            for arguments in ({"limit": 0}, {"k": 51}, {"mode": "invented"}):
                with self.assertRaises(CommandError):
                    call_command("export_ragas", **arguments)
            search.assert_not_called()

    def test_four_metrics_mapping_and_partial_nan(self):
        row = {**QUESTION, "user_input": QUESTION["question"], "response": "answer", "retrieved_contexts": ["exact context"]}
        metrics = [SimpleNamespace(ascore=AsyncMock(return_value=SimpleNamespace(value=v))) for v in (0.5, 1.0, float("nan"), -0.25)]
        results = asyncio.run(evaluator.score_samples([row], metrics))
        result = results[0]
        self.assertIsNone(result["scores"]["faithfulness"])
        self.assertEqual(result["errors"], {"faithfulness": "ValueError"})
        self.assertEqual(result["scores"]["answer_relevancy"], -0.25)
        self.assertEqual(metrics[0].ascore.call_args.kwargs["reference"], row["reference"])
        self.assertEqual(metrics[2].ascore.call_args.kwargs["retrieved_contexts"], row["retrieved_contexts"])
        self.assertEqual(set(metrics[3].ascore.call_args.kwargs), {"user_input", "response"})

    def test_partial_error_is_saved_and_nonzero_no_exception_secrets(self):
        row = {**QUESTION, "user_input": QUESTION["question"], "response": "answer", "retrieved_contexts": ["context"]}
        metrics = [SimpleNamespace(ascore=AsyncMock(side_effect=RuntimeError("Authorization Bearer secret"))) for _ in range(4)]
        results = asyncio.run(evaluator.score_samples([row], metrics))
        with tempfile.TemporaryDirectory() as directory, patch.object(evaluator, "load_ragas", return_value=()), patch.object(evaluator, "evaluate", new=AsyncMock(return_value=results)), patch("sys.stdin", io.StringIO(json.dumps(row))):
            output = Path(directory) / "scores.json"
            self.assertEqual(evaluator.main(["--output", str(output)]), 1)
            report = json.loads(output.read_text())
            self.assertEqual(report["summary"]["failed"], 1)
            self.assertNotIn("Bearer", output.read_text())
            self.assertTrue(all(v is None for v in report["results"][0]["scores"].values()))

    def test_evaluator_input_boundary(self):
        row = {**QUESTION, "user_input": "q", "response": "answer", "retrieved_contexts": ["context"]}
        for data in ("", "[]", json.dumps({**row, "response": None}), json.dumps({**row, "retrieved_contexts": []}), json.dumps({**row, "metadata": ["bad"]}), json.dumps({**row, "metadata": {"k": "5"}}), json.dumps(row) + "\n" + json.dumps(row)):
            with self.assertRaises(ValueError):
                evaluator.load_samples(data, 3)
        self.assertEqual(evaluator.load_samples(json.dumps(row), 3), [row])

    def test_optional_dependency_lazy(self):
        import builtins
        original = builtins.__import__
        def without_ragas(name, *args, **kwargs):
            if name.startswith("ragas"):
                raise ImportError("missing")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=without_ragas):
            with self.assertRaisesRegex(ValueError, "requirements-ragas"):
                evaluator.load_ragas()
            from llm.management.commands.export_ragas import Command
            self.assertIsNotNone(Command)
