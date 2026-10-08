"""Storage tests only: in-memory SQLite, no .env, network or project DB."""
from baseball.tests.api_settings import *  # noqa: F403

INSTALLED_APPS += ["travel"]  # noqa: F405
ROOT_URLCONF = "travel.knowledge_test_urls"
MIGRATION_MODULES = {name: None for name in (
    "auth", "contenttypes", "sessions", "token_blacklist", "accounts", "baseball", "travel",
)}
TIME_ZONE = "Asia/Seoul"
