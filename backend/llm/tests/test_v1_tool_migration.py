"""V1 전용 도구가 공용 llm.tools.assistant로 이전됐는지 확인한다."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from ..tools import assistant, knowledge
from ..v1.rag import domain_tools
from ..v1.rag.assistant import pipeline

SPECIALIZED = ["get_games", "get_standings", "get_ticket_prices", "get_ticket_policy",
               "get_baseball_schema", "execute_baseball_select", "search_kbo_documents",
               "search_nearby_places", "plan_course"]


class V1ToolMigrationTest(unittest.TestCase):
    def test_old_module_removed(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("llm.v1.rag.assistant.tools")

    def test_specialized_tools_defined_in_shared_module(self):
        tools = assistant.build_specialized_tools()
        self.assertEqual([t.name for t in tools], SPECIALIZED)
        for t in tools:
            if t.name != "search_kbo_documents":
                self.assertEqual(t.func.__module__, "llm.tools.assistant")
                self.assertFalse(any(k.startswith("_") for k in t.args), t.name)
        self.assertIs(pipeline.tools, assistant)

    def test_specialized_schemas_unchanged(self):
        expected = json.loads((Path(__file__).with_name("v1_tool_schemas.json")).read_text(encoding="utf-8"))
        actual = {t.name: t.args_schema.model_json_schema() for t in assistant.build_specialized_tools()}
        self.assertEqual(actual, expected)

    def test_registry_includes_place_evidence_with_specialized_precedence(self):
        tools = domain_tools.tools_for("assistant")
        names = [t.name for t in tools]
        self.assertEqual(len(names), 30)
        self.assertEqual(len(set(names)), 30)
        self.assertIn("search_place_knowledge", names)
        self.assertEqual(names[:9], SPECIALIZED)
        from llm.tools.baseball import GamesInput
        self.assertIs(tools[0].args_schema, GamesInput)

    def test_state_shared_with_knowledge_context(self):
        current = assistant.new_state("JAMSIL", "q", [])
        self.assertIs(knowledge.assistant_context.get(), current)

    def test_tools_package_does_not_import_assistant(self):
        code = ("import os, sys, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); "
                "django.setup(); import llm.tools; print('llm.tools.assistant' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        self.assertEqual(out.stdout.strip().splitlines()[-1:], ["False"], out.stderr[-500:])
