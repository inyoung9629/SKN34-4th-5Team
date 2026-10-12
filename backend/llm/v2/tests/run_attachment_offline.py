"""Run attachment/agent checks with owned shared-memory SQLite; no dotenv/live DB.

From repository root: backend/.venv/bin/python backend/llm/v2/tests/run_attachment_offline.py
Optional arguments are unittest module/class/method labels.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["WEB_RESEARCH_ENABLED"] = "false"
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
backend = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, backend)
os.environ["PYTHONPATH"] = backend

from django.conf import settings
settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                      "NAME": "file:attachment-offline?mode=memory&cache=shared", "OPTIONS": {"uri": True}}}
settings.DATABASE_ROUTERS = []
settings.ALLOWED_HOSTS = ["testserver"]
import django
django.setup()
from django.contrib.auth import get_user_model
from django.db import connection
from django.core.management import call_command as original_command
from llm.models import ChatSession, ChatAttachment

with connection.schema_editor() as editor:
    for model in (get_user_model(), ChatSession, ChatAttachment):
        editor.create_model(model)


def command(name, *args, **kwargs):
    if name == "flush":
        # Only these owned tables exist; unrelated app schemas include PostgreSQL-only indexes.
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM llm_chatattachment')
            cursor.execute('DELETE FROM llm_chatsession')
    else:
        return original_command(name, *args, **kwargs)


labels = sys.argv[1:] or ["llm.v2.tests.test_attachment_delegation", "llm.v2.tests.test_chain",
                         "llm.v2.tests.test_browser_research", "llm.v2.tests.test_web_specialist",
                         "llm.v2.tests.test_web_mcp_direct"]
with patch("django.test.testcases.call_command", side_effect=command):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(labels))
raise SystemExit(not result.wasSuccessful())
