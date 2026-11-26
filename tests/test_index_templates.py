"""Verify index.html template assembly and static JS asset registration."""

import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-secret-token")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vibeone.routes.static_assets import _STATIC_MEDIA_TYPES, render_index_html  # noqa: E402


class IndexTemplateAssemblyTest(unittest.TestCase):
    def test_render_index_includes_modals_and_valid_static_scripts(self):
        html = render_index_html()
        self.assertNotIn("<!-- INCLUDE:", html)
        for marker in (
            'id="dir-modal"',
            'id="model-modal"',
            'id="diff-modal"',
            'id="file-preview-modal"',
            'id="settings-modal"',
            'id="image-modal"',
            'id="profile-login-modal"',
            'content="black"',
        ):
            self.assertIn(marker, html)

        scripts = re.findall(r'<script\s+src="/static/([^"]+)"', html)
        self.assertGreater(len(scripts), 15)
        for name in scripts:
            self.assertIn(name, _STATIC_MEDIA_TYPES, f"Unregistered static script: {name}")
            self.assertTrue((ROOT / "static" / name).is_file(), f"Missing static script: {name}")


if __name__ == "__main__":
    unittest.main()
