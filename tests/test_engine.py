from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from codex_session_manager.app_server import ForkResult
from codex_session_manager.audit import AuditStore
from codex_session_manager.engine import MigrationEngine, MigrationError
from codex_session_manager.repository import CodexRepository


class FakeForkAdapter:
    def __init__(self, repository: CodexRepository):
        self.repository = repository

    def fork(self, thread_id: str, target_provider: str) -> ForkResult:
        source = self.repository.sessions_by_id([thread_id])[0]
        new_id = "fork-session-1"
        new_path = Path(source.rollout_path).with_name(f"rollout-fork-{new_id}.jsonl")
        lines = []
        for line in Path(source.rollout_path).read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if value.get("type") == "session_meta":
                value["payload"]["id"] = new_id
                value["payload"]["model_provider"] = target_provider
                value["payload"]["forked_from_id"] = thread_id
            lines.append(json.dumps(value, separators=(",", ":")))
        new_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with sqlite3.connect(source.db_path) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(threads)")]
            select = [
                "?"
                if column in {"id", "rollout_path", "model_provider"}
                else column
                for column in columns
            ]
            conn.execute(
                f"INSERT INTO threads ({', '.join(columns)}) "
                f"SELECT {', '.join(select)} FROM threads WHERE id = ?",
                (new_id, str(new_path), target_provider, thread_id),
            )
        return ForkResult(new_id, str(new_path), target_provider)


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
        repository = CodexRepository(self.codex_home)
        self.engine = MigrationEngine(
            repository, AuditStore(self.root / "manager"), FakeForkAdapter(repository)
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_workspace_snapshot_uses_bounded_session_summaries(self) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE threads SET title = ? WHERE id = 'session-1'", ("x" * 1000,))
        snapshot = self.engine.workspace_snapshot()
        summary = snapshot["sessions"][0]
        self.assertEqual(len(summary["title"]), 240)
        self.assertTrue(summary["title_truncated"])
        self.assertNotIn("rollout_path", summary)
        self.assertEqual(self.engine.repository.session_title("session-1"), "x" * 1000)

    def test_preview_execute_and_restore_round_trip(self) -> None:
        plan = self.engine.preview(["session-1"], "source", "target")
        self.assertTrue(plan.executable)
        self.assertGreater(plan.estimated_backup_bytes, 0)
        self.assertIn("provider-provenance-unavailable", {risk.code for risk in plan.risks})

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

        restore_plan = self.engine.preview_restore(migration["operation_id"])
        self.assertFalse(restore_plan["executable"])
        self.assertIn("trace-diverged", {risk["code"] for risk in restore_plan["risks"]})
        with self.assertRaisesRegex(MigrationError, "阻断风险"):
            self.engine.restore(migration["operation_id"], "RESTORE")

    def test_preview_reports_encrypted_reasoning(self) -> None:
        with self.rollout.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "reasoning", "encrypted_content": "opaque"},
                    }
                )
                + "\n"
            )
        plan = self.engine.preview(["session-1"], "source", "target")
        self.assertEqual(plan.trace_profiles[0].encrypted_content_items, 1)
        self.assertIn("encrypted-content-not-portable", {risk.code for risk in plan.risks})

    def test_fork_preserves_source_and_can_be_undone(self) -> None:
        fork = self.engine.fork("session-1", "target", "FORK")
        self.assertEqual(fork["status"], "completed")
        forked_id = fork["forked_session_ids"][0]
        sessions = {session.id: session for session in self.engine.repository.scan_sessions()}
        self.assertEqual(sessions["session-1"].provider, "source")
        self.assertEqual(sessions[forked_id].provider, "target")

        restore_plan = self.engine.preview_restore(fork["operation_id"])
        self.assertTrue(restore_plan["executable"])
        restored = self.engine.restore(fork["operation_id"], "RESTORE")
        self.assertEqual(restored["status"], "completed")
        self.assertNotIn(forked_id, {session.id for session in self.engine.repository.scan_sessions()})

    def test_multi_fork_preview_warns_that_batch_is_not_atomic(self) -> None:
        second_rollout = self.rollout.with_name("rollout-session-2.jsonl")
        second_rollout.write_text(
            self.rollout.read_text(encoding="utf-8").replace("session-1", "session-2"),
            encoding="utf-8",
        )
        with sqlite3.connect(self.db) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(threads)")]
            select = ["?" if column in {"id", "rollout_path"} else column for column in columns]
            conn.execute(
                f"INSERT INTO threads ({', '.join(columns)}) "
                f"SELECT {', '.join(select)} FROM threads WHERE id = ?",
                ("session-2", str(second_rollout), "session-1"),
            )
        plan = self.engine.preview_forks(["session-1", "session-2"], "target")
        self.assertTrue(plan.executable)
        self.assertEqual(
            plan.estimated_backup_bytes,
            self.rollout.stat().st_size + second_rollout.stat().st_size + (2 * self.db.stat().st_size),
        )
        self.assertIn("source-preserved", {risk.code for risk in plan.risks})
        self.assertIn("fork-batch-non-atomic", {risk.code for risk in plan.risks})

    def test_fork_undo_blocks_after_new_chat(self) -> None:
        fork = self.engine.fork("session-1", "target", "FORK")
        fork_path = Path(fork["created_files"][0]["source"])
        with fork_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {"type": "new"}}) + "\n")
        self.assertFalse(self.engine.preview_restore(fork["operation_id"])["executable"])

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
