import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class PublicConfigTest(unittest.TestCase):
    def read_defaults(self, **overrides):
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("VIBEONE_", "AGY_"))
        }
        env.update(VIBEONE_WEB_TOKEN="test-token", **overrides)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; from vibeone import config; "
                "print(json.dumps([config.VIBEONE_LAUNCHD_LABEL, config.VIBEONE_VAPID_SUBJECT]))",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return json.loads(result.stdout)

    def test_portable_defaults(self):
        self.assertEqual(self.read_defaults(), ["local.vibeone", "mailto:admin@example.com"])

    def test_deployment_overrides(self):
        self.assertEqual(
            self.read_defaults(
                VIBEONE_LAUNCHD_LABEL="test.vibeone",
                VIBEONE_VAPID_SUBJECT="mailto:push@example.com",
            ),
            ["test.vibeone", "mailto:push@example.com"],
        )
