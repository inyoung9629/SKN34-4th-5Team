"""Run directly: python -m unittest discover -s backend/llm/tests -p test_usage_compose.py."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
DEFAULTS = {"USAGE_GUEST_TOKENS": "500000", "USAGE_MEMBER_MONTHLY_TOKENS": "999999000"}


class UsageComposeTest(unittest.TestCase):
    def test_prod_allowances_reach_settings_without_real_env(self):
        compose = (ROOT / "docker-compose.prod.yml").read_text()
        required = set(re.findall(r"\$\{([A-Z_]+):\?", compose))
        settings = ast.parse((ROOT / "backend/config/settings.py").read_text())
        assignments = ast.Module(body=[node for node in settings.body if isinstance(node, ast.Assign)
                                      and any(isinstance(target, ast.Name) and target.id in DEFAULTS
                                              for target in node.targets)], type_ignores=[])
        example = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines()
                       if line.startswith(tuple(f"{name}=" for name in DEFAULTS)))
        self.assertEqual(example, DEFAULTS)
        # Copy only public Compose text, so Compose cannot discover the real root .env.
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "compose.yml"
            fixture = Path(directory) / "fixture.env"
            config.write_text(compose)
            for overrides, expected in (
                ({}, DEFAULTS),
                ({name: "" for name in DEFAULTS}, DEFAULTS),
                ({"USAGE_GUEST_TOKENS": "12345", "USAGE_MEMBER_MONTHLY_TOKENS": "67890"},
                 {"USAGE_GUEST_TOKENS": "12345", "USAGE_MEMBER_MONTHLY_TOKENS": "67890"}),
            ):
                with self.subTest(overrides=overrides):
                    fixture.write_text("\n".join(f"{name}={value}" for name, value in
                                               {**dict.fromkeys(required, "test-only"), **overrides}.items()))
                    result = subprocess.run(
                        ["docker", "compose", "--env-file", str(fixture), "-f", str(config),
                         "config", "--format", "json"], cwd=directory,
                        env={"PATH": os.environ.get("PATH", ""), "COMPOSE_DISABLE_ENV_FILE": "1"},
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    environment = json.loads(result.stdout)["services"]["backend"]["environment"]
                    actual = {name: environment[name] for name in DEFAULTS}
                    self.assertEqual(actual, expected)
                    namespace = {"os": os}
                    with patch.dict(os.environ, actual, clear=True):
                        exec(compile(assignments, "usage settings", "exec"), namespace)
                    self.assertEqual({name: namespace[name] for name in DEFAULTS},
                                     {name: int(value) for name, value in expected.items()})


if __name__ == "__main__":
    unittest.main()
