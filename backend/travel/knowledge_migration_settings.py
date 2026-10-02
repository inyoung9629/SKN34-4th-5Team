"""Inspect migration state only; never use this to deploy a database."""
from .knowledge_test_settings import *  # noqa: F403

INSTALLED_APPS += ["tving"]  # noqa: F405
MIGRATION_MODULES = {}
