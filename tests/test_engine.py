from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from codex_session_manager.audit import AuditStore
from codex_session_manager.engine import MigrationEngine, MigrationError
from codex_session_manager.repository import CodexRepository


class MigrationEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.codex_home = self.root / "codex"
        self.codex_home.mkdir()
        (self.codex_home / "config.toml").write_text(
            '[model_providers.target]\nname = "Target"\nbase_url = "https://example.invalid"\n',
            encoding="utf-8",
        )
        session_dir = self.codex_home / "sessions" / "2026" / "08" / "18"
        session_dir.mkdir(parents=True)
        self.rollout = session_dir / "rollout-session-1.jsonl"
        self.rollout.write_text(
            json.dumps(
                {
                    "timestamp": "2026-08-18T00:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "session-1",
                        "cwd": "/tmp/project",
                        "model_provider": "source",
                    },
                },
                separators=(",", ":"),
            )
            + "\n"
            + json.dumps({"type": "response_item", "payload": {"role": "user"}})
            + "\n",
            encoding="utf-8",
        )
        self.db = self.codex_home / "state_5.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    rollout_path TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    model_provider TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    title TEXT NOT NULL,
                    sandbox_policy TEXT NOT NULL,
                    approval_mode TEXT NOT NULL,
                    model TEXT,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """INSERT INTO threads
                   (id, rollout_path, created_at, updated_at, source, model_provider,
                    cwd, title, sandbox_policy, approval_mode, model, archived)
                   VALUES (?, ?, 1, 2, 'cli', 'source', '/tmp/project', 'Test session',
                           'workspace-write', 'on-request', 'gpt-test', 0)""",
                ("session-1", str(self.rollout)),
            )
        self.engine = MigrationEngine(
            CodexRepository(self.codex_home), AuditStore(self.root / "manager")
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preview_execute_and_restore_round_trip(self) -> None:
        plan = self.engine.preview(["session-1"], "source", "target")
        self.assertTrue(plan.executable)
        self.assertGreater(plan.estimated_backup_bytes, 0)

        migration = self.engine.execute(["session-1"], "source", "target", "MIGRATE")
        self.assertEqual(migration["status"], "completed")
        session = self.engine.repository.scan_sessions()[0]
        self.assertEqual((session.provider, session.rollout_provider), ("target", "target"))

        restore = self.engine.restore(migration["operation_id"], "RESTORE")
        self.assertEqual(restore["status"], "completed")
        session = self.engine.repository.scan_sessions()[0]
        self.assertEqual((session.provider, session.rollout_provider), ("source", "source"))

        operations = self.engine.audit.list_operations()
        self.assertEqual(len(operations), 2)
        self.assertTrue((self.root / "manager" / "audit.jsonl").exists())
        self.assertTrue(self.engine.audit.verify_chain())

    def test_restore_refuses_external_changes(self) -> None:
        migration = self.engine.execute(["session-1"], "source", "target", "MIGRATE")
        with self.rollout.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {"type": "new"}}) + "\n")

        with self.assertRaisesRegex(MigrationError, "changed after migration"):
            self.engine.restore(migration["operation_id"], "RESTORE")

    def test_execute_requires_exact_acknowledgement(self) -> None:
        with self.assertRaisesRegex(MigrationError, "MIGRATE"):
            self.engine.execute(["session-1"], "source", "target", "yes")

    def test_metadata_mismatch_is_critical(self) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE threads SET model_provider = 'other'")
        plan = self.engine.preview(["session-1"], "other", "target")
        self.assertFalse(plan.executable)
        self.assertIn("metadata-mismatch", {risk.code for risk in plan.risks})

    def test_audit_chain_detects_tampering(self) -> None:
        migration = self.engine.execute(["session-1"], "source", "target", "MIGRATE")
        self.assertEqual(migration["status"], "completed")
        audit_path = self.root / "manager" / "audit.jsonl"
        audit_path.write_text(
            audit_path.read_text(encoding="utf-8").replace('"status": "completed"', '"status": "edited"'),
            encoding="utf-8",
        )
        self.assertFalse(self.engine.audit.verify_chain())


if __name__ == "__main__":
    unittest.main()
