from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_transfer.app_server import ForkResult
from codex_transfer.audit import AuditStore
from codex_transfer.fleet import DesktopSshDiscovery, HostDescriptor, HostFleet
from codex_transfer.model import Session
from codex_transfer.repository import CodexRepository


class FakeHost:
    def __init__(self, host_id: str, sessions: list[Session], payloads: dict[str, bytes]):
        self.descriptor = HostDescriptor(host_id, host_id, "fake")
        self._sessions = {session.id: session for session in sessions}
        self.payloads = dict(payloads)
        self.providers = {"source", "target"}

    def sessions(self):
        return list(self._sessions.values())

    def provider_ids(self):
        return sorted(self.providers)

    def fetch_rollout(self, path):
        return self.payloads[path]

    def stage_rollout(self, operation_id, session_id, payload):
        path = f"/{self.descriptor.id}/staging/{operation_id}-{session_id}.jsonl"
        self.payloads[path] = payload
        return path

    def remove_staged_rollout(self, path):
        self.payloads.pop(path, None)

    def fork_from_path(self, path, provider, cwd):
        source = self.payloads[path]
        new_id = f"fork-{len(self._sessions) + 1}"
        records = []
        for line in source.splitlines():
            value = json.loads(line)
            if value.get("type") == "session_meta":
                value["payload"]["id"] = new_id
                value["payload"]["model_provider"] = provider
            records.append(json.dumps(value, separators=(",", ":")))
        payload = ("\n".join(records) + "\n").encode()
        rollout = f"/{self.descriptor.id}/sessions/{new_id}.jsonl"
        self.payloads[rollout] = payload
        self._sessions[new_id] = Session(
            id=new_id,
            title="Imported",
            provider=provider,
            model="gpt-test",
            cwd=cwd,
            updated_at=2,
            rollout_path=rollout,
            db_path=f"/{self.descriptor.id}/state.sqlite",
            archived=False,
            locked=False,
            rollout_provider=provider,
            size_bytes=len(payload),
            host_id=self.descriptor.id,
        )
        return ForkResult(new_id, rollout, provider)

    def set_archived(self, thread_id, archived):
        self._sessions[thread_id] = replace(self._sessions[thread_id], archived=archived)

    def delete_thread(self, thread_id):
        session = self._sessions.pop(thread_id)
        self.payloads.pop(session.rollout_path, None)

    def cwd_exists(self, path):
        return path == "/target/project"

    def integrity_check(self):
        return "ok"

    def backup_databases(self, audit, operation_dir, start_index):
        return []


class FleetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        codex_home = root / "codex"
        codex_home.mkdir()
        payload = (
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": "session-1", "model_provider": "source"},
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        source_session = Session(
            id="session-1",
            title="Source",
            provider="source",
            model="gpt-test",
            cwd="/source/project",
            updated_at=1,
            rollout_path="/source/sessions/session-1.jsonl",
            db_path="/source/state.sqlite",
            archived=False,
            locked=False,
            rollout_provider="source",
            size_bytes=len(payload),
            host_id="source-host",
        )
        self.source = FakeHost("source-host", [source_session], {source_session.rollout_path: payload})
        self.target = FakeHost("target-host", [], {})
        self.fleet = HostFleet(
            CodexRepository(codex_home),
            AuditStore(root / "audit"),
            object(),
            adapters={"source-host": self.source, "target-host": self.target},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_discovery_only_accepts_desktop_proxy_processes(self):
        process_table = """10 1 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT
11 10 /usr/bin/ssh -T G1 sh -c 'exec codex app-server proxy'
12 1 /usr/bin/ssh -T ignored sh -c 'exec codex app-server proxy'
13 10 /usr/bin/ssh ordinary
"""
        with patch(
            "codex_transfer.fleet.subprocess.run",
            return_value=SimpleNamespace(stdout=process_table),
        ):
            self.assertEqual(DesktopSshDiscovery().aliases(), ["G1"])

    def test_cross_host_fork_preserves_source(self):
        plan = self.fleet.preview_transfer(
            ["session-1"], "source-host", "target-host", "target", "/target/project", False
        )
        self.assertTrue(plan["executable"])
        result = self.fleet.transfer_batch(
            ["session-1"], "source-host", "target-host", "target", "/target/project", False, "FORK"
        )
        self.assertIsNone(result["failed"])
        self.assertFalse(self.source.sessions()[0].archived)
        self.assertEqual(self.target.sessions()[0].provider, "target")

    def test_cross_host_move_archives_source_and_restores(self):
        result = self.fleet.transfer_batch(
            ["session-1"], "source-host", "target-host", "target", "/target/project", True, "MIGRATE"
        )
        operation = result["completed"][0]
        self.assertTrue(self.source.sessions()[0].archived)
        self.assertTrue(self.fleet.preview_restore(operation["operation_id"])["executable"])
        restored = self.fleet.restore(operation["operation_id"], "RESTORE")
        self.assertEqual(restored["status"], "completed")
        self.assertFalse(self.source.sessions()[0].archived)
        self.assertEqual(self.target.sessions(), [])

    def test_remote_archive_and_unarchive_are_backed_up_and_audited(self):
        plan = self.fleet.preview_archive(["session-1"], "source-host", True)
        self.assertTrue(plan["executable"])
        self.assertEqual(plan["host_id"], "source-host")
        self.assertGreater(plan["estimated_backup_bytes"], 0)

        archived = self.fleet.set_archived_batch(
            ["session-1"], "source-host", True, "ARCHIVE"
        )
        self.assertIsNone(archived["failed"])
        self.assertTrue(self.source.sessions()[0].archived)
        archive_manifest = self.fleet.audit.read_manifest(
            archived["completed"][0]["operation_id"]
        )
        self.assertEqual(archive_manifest["host_id"], "source-host")
        self.assertTrue(archive_manifest["files"])
        self.assertTrue(archive_manifest["post_files"])

        unarchived = self.fleet.set_archived_batch(
            ["session-1", "session-1"], "source-host", False, "UNARCHIVE"
        )
        self.assertIsNone(unarchived["failed"])
        self.assertEqual(len(unarchived["completed"]), 1)
        self.assertFalse(self.source.sessions()[0].archived)
        self.assertEqual(
            self.fleet.audit.read_manifest(
                unarchived["completed"][0]["operation_id"]
            )["kind"],
            "unarchive",
        )


if __name__ == "__main__":
    unittest.main()
