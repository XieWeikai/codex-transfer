from __future__ import annotations

import unittest

from codex_session_manager.server import SessionManagerHandler


class StaticAssetsTest(unittest.TestCase):
    def test_assets_are_packaged(self) -> None:
        self.assertIn(b"Codex Session Manager", SessionManagerHandler._static("index.html"))
        self.assertIn(b"/api/preview", SessionManagerHandler._static("app.js"))


if __name__ == "__main__":
    unittest.main()

