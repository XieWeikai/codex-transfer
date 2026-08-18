from __future__ import annotations

import unittest

from codex_session_manager.server import SessionManagerHandler


class StaticAssetsTest(unittest.TestCase):
    def test_assets_are_packaged(self) -> None:
        self.assertIn(b"Codex Session Manager", SessionManagerHandler._static("index.html"))
        self.assertIn(b"/api/preview", SessionManagerHandler._static("app.js"))

    def test_workbench_includes_drag_and_click_paths(self) -> None:
        html = SessionManagerHandler._static("index.html")
        script = SessionManagerHandler._static("app.js")
        self.assertIn("迁移投放区".encode(), html)
        self.assertIn("确认迁移风险".encode(), html)
        self.assertIn("正在检查快照与当前状态".encode(), html)
        self.assertNotIn("安全说明".encode(), html)
        self.assertIn(b'addEventListener("drop"', script)
        self.assertIn("加入".encode(), script)
        self.assertIn(b"prefers-reduced-motion", SessionManagerHandler._static("styles.css"))


if __name__ == "__main__":
    unittest.main()
