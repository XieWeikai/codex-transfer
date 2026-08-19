from __future__ import annotations

import unittest

from codex_transfer.model import Session
from codex_transfer.server import CodexTransferHandler


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
        self.assertIn(b"Codex Transfer", CodexTransferHandler._static("index.html"))
        self.assertIn(b"/api/workspace", CodexTransferHandler._static("app.js"))
        self.assertNotIn(b"pollHosts", CodexTransferHandler._static("app.js"))
        self.assertIn(b"refresh_host", CodexTransferHandler._static("app.js"))
        self.assertIn(b'aria-busy', CodexTransferHandler._static("app.js"))
        self.assertIn(b"/api/events", CodexTransferHandler._static("app.js"))
        self.assertIn(b"/api/session-locks", CodexTransferHandler._static("app.js"))
        self.assertIn(b"providerPopover", CodexTransferHandler._static("app.js"))
        self.assertIn(b"host?.providers || []", CodexTransferHandler._static("app.js"))
        self.assertIn(b"availableProviders.includes(state.activeProvider)", CodexTransferHandler._static("app.js"))
        self.assertIn(b"/api/preview", CodexTransferHandler._static("app.js"))
        self.assertIn(b"/api/fork", CodexTransferHandler._static("app.js"))
        self.assertIn(b"/api/archive", CodexTransferHandler._static("app.js"))
        self.assertIn(b"/api/transfer", CodexTransferHandler._static("app.js"))
        self.assertIn(b"thread/fork", CodexTransferHandler._static("docs.html"))
        self.assertIn(b'id="provider-model"', CodexTransferHandler._static("docs.html"))
        self.assertIn("Provider 是一条有名字的运行时路线".encode(), CodexTransferHandler._static("docs.html"))
        self.assertIn(b"provider-chain", CodexTransferHandler._static("docs.css"))
        self.assertIn(b"codex-transfer-theme", CodexTransferHandler._static("docs.js"))
        self.assertIn(b'id="docsLanguageSelect"', CodexTransferHandler._static("docs.html"))
        self.assertIn(b"A provider is a named runtime route", CodexTransferHandler._static("docs_en.html"))
        self.assertIn(b"codex-transfer-locale", CodexTransferHandler._static("i18n.js"))

    def test_workbench_includes_drag_and_click_paths(self) -> None:
        html = CodexTransferHandler._static("index.html")
        script = CodexTransferHandler._static("app.js")
        self.assertIn("Fork 工作区".encode(), html)
        self.assertIn("确认 Fork 风险".encode(), html)
        self.assertIn("正在检查快照与当前状态".encode(), html)
        self.assertNotIn("安全说明".encode(), html)
        self.assertIn(b'addEventListener("drop"', script)
        self.assertIn("支持多选".encode(), script)
        self.assertIn(b'id="projectFilter"', html)
        self.assertIn(b'id="sourceHost"', html)
        self.assertIn(b'id="targetHost"', html)
        self.assertIn(b'id="targetCwd"', html)
        self.assertIn(b"session-card", script)
        self.assertIn(b'id="sessionPopover"', html)
        self.assertIn(b"/api/forks/preview", script)
        self.assertIn(b"sessions.map", script)
        self.assertIn(b"prefers-reduced-motion", CodexTransferHandler._static("styles.css"))
        self.assertIn(b'data-action="fork"', html)
        self.assertNotIn(b'data-action="archive"', html)
        self.assertNotIn(b'data-action="unarchive"', html)
        self.assertIn(b"archive-button", script)
        self.assertIn(b"host_id: state.activeHost", script)
        self.assertIn(b'themeSelect', html)
        self.assertIn(b'id="languageSelect"', html)
        self.assertIn(b'src="/i18n.js"', html)
        self.assertIn(b'row.addEventListener("dragstart"', script)
        self.assertIn(b'user-select: none', CodexTransferHandler._static("styles.css"))
        self.assertIn("Codex 持有 writer lock".encode(), script)
        self.assertIn('<span class="status-chip locked">占用</span>'.encode(), script)
        self.assertIn("凭据、Token、Header 值和查询参数值不会进入浏览器响应".encode(), script)


if __name__ == "__main__":
    unittest.main()
