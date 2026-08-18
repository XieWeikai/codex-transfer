from __future__ import annotations

import unittest

from codex_session_manager.model import Session
from codex_session_manager.server import SessionManagerHandler


class StaticAssetsTest(unittest.TestCase):
    def test_session_summary_bounds_large_titles(self) -> None:
        session = Session(
            id="session-1",
            title="x" * 1000,
            provider="source",
            model="model",
            cwd="/tmp/project",
            updated_at=1,
            rollout_path="/tmp/rollout.jsonl",
            db_path="/tmp/state.sqlite",
            archived=False,
            locked=False,
            rollout_provider="source",
            size_bytes=10,
        )
        summary = session.to_summary_dict()
        self.assertEqual(len(summary["title"]), 240)
        self.assertTrue(summary["title_truncated"])
        self.assertNotIn("rollout_path", summary)
        self.assertNotIn("db_path", summary)

    def test_assets_are_packaged(self) -> None:
        self.assertIn(b"Codex Relay", SessionManagerHandler._static("index.html"))
        self.assertIn(b"/api/workspace", SessionManagerHandler._static("app.js"))
        self.assertIn(b"/api/preview", SessionManagerHandler._static("app.js"))
        self.assertIn(b"/api/fork", SessionManagerHandler._static("app.js"))
        self.assertIn(b"/api/archive", SessionManagerHandler._static("app.js"))
        self.assertIn(b"thread/fork", SessionManagerHandler._static("docs.html"))
        self.assertIn(b"codex-relay-theme", SessionManagerHandler._static("docs.js"))

    def test_workbench_includes_drag_and_click_paths(self) -> None:
        html = SessionManagerHandler._static("index.html")
        script = SessionManagerHandler._static("app.js")
        self.assertIn("Fork 工作区".encode(), html)
        self.assertIn("确认 Fork 风险".encode(), html)
        self.assertIn("正在检查快照与当前状态".encode(), html)
        self.assertNotIn("安全说明".encode(), html)
        self.assertIn(b'addEventListener("drop"', script)
        self.assertIn("支持多选".encode(), script)
        self.assertIn(b'id="projectFilter"', html)
        self.assertIn(b"session-card", script)
        self.assertIn(b'id="sessionPopover"', html)
        self.assertIn(b"/api/forks/preview", script)
        self.assertIn(b"sessions.map", script)
        self.assertIn(b"prefers-reduced-motion", SessionManagerHandler._static("styles.css"))
        self.assertIn(b'data-action="fork"', html)
        self.assertIn(b'data-action="archive"', html)
        self.assertIn(b'data-action="unarchive"', html)
        self.assertIn(b'themeSelect', html)
        self.assertIn(b'row.addEventListener("dragstart"', script)
        self.assertIn(b'user-select: none', SessionManagerHandler._static("styles.css"))


if __name__ == "__main__":
    unittest.main()
