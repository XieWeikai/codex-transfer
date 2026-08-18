from __future__ import annotations

import io
import json
import unittest
from pathlib import Path

from codex_session_manager.cli import execute_command, main, parser
from codex_session_manager.engine import MigrationError
from codex_session_manager.model import Session


def session(
    session_id: str,
    title: str,
    provider: str,
    cwd: str,
    updated_at: int,
    *,
    locked: bool = False,
    archived: bool = False,
) -> Session:
    return Session(
        id=session_id,
        title=title,
        provider=provider,
        model="gpt-test",
        cwd=cwd,
        updated_at=updated_at,
        rollout_path=f"/tmp/{session_id}.jsonl",
        db_path="/tmp/state.sqlite",
        archived=archived,
        locked=locked,
        rollout_provider=provider,
        size_bytes=updated_at * 10,
    )


class FakePlan:
    def __init__(self, payload: dict):
        self.payload = payload

    def to_dict(self) -> dict:
        return self.payload

    @property
    def executable(self) -> bool:
        return bool(self.payload.get("executable"))


class FakeRepository:
    def __init__(self):
        self.sessions = [
            session("s-1", "Alpha", "source", "/work/one", 3),
            session("s-2", "Beta", "source", "/work/two", 2, locked=True),
            session("s-3", "Gamma", "target", "/work/one", 1, archived=True),
        ]

    def scan_sessions(self) -> list[Session]:
        return self.sessions


class FakeAudit:
    def list_operations(self) -> list[dict]:
        return [
            {"operation_id": "op-2", "kind": "fork", "status": "completed"},
            {"operation_id": "op-1", "kind": "migration", "status": "completed"},
        ]


class FakeEngine:
    def __init__(self):
        self.repository = FakeRepository()
        self.audit = FakeAudit()
        self.calls = []

    def status(self) -> dict:
        return {
            "codex_home": "/tmp/codex",
            "data_dir": "/tmp/backups",
            "databases": [],
            "session_count": 3,
            "locked_session_count": 1,
            "providers": ["source", "target"],
            "audit_chain_valid": True,
        }

    def preview_forks(self, session_ids: list[str], target: str) -> FakePlan:
        self.calls.append(("fork-preview", session_ids, target))
        return FakePlan({"sessions": session_ids, "risks": [], "executable": True})

    def preview(self, session_ids: list[str], source: str, target: str) -> FakePlan:
        self.calls.append(("move-preview", session_ids, source, target))
        return FakePlan({"sessions": session_ids, "risks": [], "executable": True})

    def fork(self, session_id: str, target: str, acknowledgement: str) -> dict:
        self.calls.append(("fork", session_id, target, acknowledgement))
        if session_id == "bad":
            raise MigrationError("preflight blocked")
        return {
            "operation_id": f"fork-{session_id}",
            "forked_session_ids": [f"new-{session_id}"],
        }

    def execute(
        self,
        session_ids: list[str],
        source: str,
        target: str,
        acknowledgement: str,
    ) -> dict:
        self.calls.append(("move", session_ids, source, target, acknowledgement))
        return {"operation_id": "move-1", "kind": "migration", "status": "completed"}

    def preview_restore(self, operation_id: str) -> dict:
        self.calls.append(("restore-preview", operation_id))
        return {"operation_id": operation_id, "session_ids": ["s-1"], "risks": [], "executable": True}

    def restore(self, operation_id: str, acknowledgement: str) -> dict:
        self.calls.append(("restore", operation_id, acknowledgement))
        return {"operation_id": "restore-1", "kind": "restore", "status": "completed"}

    def preview_archive(self, session_ids: list[str], archived: bool) -> dict:
        self.calls.append(("archive-preview", session_ids, archived))
        return {"sessions": session_ids, "archived": archived, "risks": [], "executable": True}

    def set_archived_batch(
        self, session_ids: list[str], archived: bool, acknowledgement: str
    ) -> dict:
        self.calls.append(("archive", session_ids, archived, acknowledgement))
        return {
            "requested_session_ids": session_ids,
            "archived": archived,
            "completed": [{"operation_id": "archive-1", "session_ids": session_ids}],
            "failed": None,
            "batch_atomic": False,
        }


class CliTest(unittest.TestCase):
    def test_legacy_and_explicit_serve_arguments_are_supported(self) -> None:
        legacy = parser().parse_args(["--port", "9000"])
        explicit = parser().parse_args(["serve", "--port", "9001"])
        self.assertIsNone(legacy.command)
        self.assertEqual(legacy.port, 9000)
        self.assertEqual(explicit.command, "serve")
        self.assertEqual(explicit.port, 9001)

    def test_command_storage_options_work_after_subcommand(self) -> None:
        args = parser().parse_args(
            ["sessions", "--codex-home", "/tmp/custom", "--data-dir", "/tmp/audit"]
        )
        self.assertEqual(args.codex_home, Path("/tmp/custom"))
        self.assertEqual(args.data_dir, Path("/tmp/audit"))

    def test_sessions_match_web_filters_and_sorting(self) -> None:
        args = parser().parse_args(
            [
                "sessions",
                "--provider",
                "source",
                "--project",
                "/work/one",
                "--status",
                "ready",
                "--search",
                "alp",
                "--sort",
                "oldest",
            ]
        )
        result, code = execute_command(args, FakeEngine())
        self.assertEqual(code, 0)
        self.assertEqual([item["id"] for item in result["sessions"]], ["s-1"])

    def test_json_output_is_machine_readable(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        engine = FakeEngine()
        code = main(
            ["sessions", "--status", "locked", "--json"],
            engine_factory=lambda _args: engine,
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["sessions"][0]["id"], "s-2")
        self.assertEqual(stderr.getvalue(), "")

    def test_fork_batch_stops_after_failure_and_reports_partial_result(self) -> None:
        args = parser().parse_args(
            [
                "fork",
                "--session",
                "s-1",
                "--session",
                "bad",
                "--session",
                "s-3",
                "--target",
                "target",
                "--acknowledge",
                "FORK",
            ]
        )
        engine = FakeEngine()
        result, code = execute_command(args, engine)
        self.assertEqual(code, 1)
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(result["failed"]["session_id"], "bad")
        self.assertEqual(engine.calls[0], ("fork-preview", ["s-1", "bad", "s-3"], "target"))
        self.assertNotIn(("fork", "s-3", "target", "FORK"), engine.calls)

    def test_move_and_restore_require_exact_acknowledgement(self) -> None:
        move = parser().parse_args(
            [
                "move",
                "--session",
                "s-1",
                "--source",
                "source",
                "--target",
                "target",
                "--acknowledge",
                "MIGRATE",
            ]
        )
        restore = parser().parse_args(
            ["restore", "--operation", "op-1", "--acknowledge", "RESTORE"]
        )
        engine = FakeEngine()
        execute_command(move, engine)
        execute_command(restore, engine)
        self.assertIn(("move", ["s-1"], "source", "target", "MIGRATE"), engine.calls)
        self.assertIn(("restore", "op-1", "RESTORE"), engine.calls)

    def test_archive_and_unarchive_commands_route_to_shared_engine(self) -> None:
        preview = parser().parse_args(
            ["archive-preview", "--session", "s-1", "--session", "s-2"]
        )
        unarchive = parser().parse_args(
            [
                "unarchive",
                "--session",
                "s-3",
                "--acknowledge",
                "UNARCHIVE",
            ]
        )
        engine = FakeEngine()
        preview_result, preview_code = execute_command(preview, engine)
        result, code = execute_command(unarchive, engine)
        self.assertTrue(preview_result["archived"])
        self.assertEqual(preview_code, 0)
        self.assertFalse(result["archived"])
        self.assertEqual(code, 0)
        self.assertIn(("archive-preview", ["s-1", "s-2"], True), engine.calls)
        self.assertIn(("archive", ["s-3"], False, "UNARCHIVE"), engine.calls)


if __name__ == "__main__":
    unittest.main()
