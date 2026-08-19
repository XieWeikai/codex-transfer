from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_transfer.app_server import ForkResult
from codex_transfer.audit import AuditStore
from codex_transfer.fleet import (
    DesktopSshDiscovery,
    HostDescriptor,
    HostFleet,
    SshHostAdapter,
)
from codex_transfer.model import Session
from codex_transfer.repository import CodexRepository
from codex_transfer.providers import provider_catalog


class FakeHost:
    def __init__(self, host_id: str, sessions: list[Session], payloads: dict[str, bytes]):
        self.descriptor = HostDescriptor(host_id, host_id, "fake")
        self._sessions = {session.id: session for session in sessions}
        self.payloads = dict(payloads)
        self.providers = {"source", "target"}
        self.session_reads = 0
        self.provider_reads = 0
        self.failure = None

    def sessions(self):
        self.session_reads += 1
        if self.failure:
            raise RuntimeError(self.failure)
        return list(self._sessions.values())

    def provider_ids(self):
        return sorted(self.providers)

    def provider_details(self, sessions):
        self.provider_reads += 1
        return provider_catalog(
            {"model_providers": {provider: {} for provider in self.providers}},
            sessions,
            host_id=self.descriptor.id,
            config_source=f"/{self.descriptor.id}/config.toml",
        )

    def fetch_rollout(self, path):
        return self.payloads[path]

    def stage_rollout(self, operation_id, session_id, payload):
        path = f"/{self.descriptor.id}/staging/{operation_id}-{session_id}.jsonl"
        self.payloads[path] = payload
        return path

    def remove_staged_rollout(self, path):
        self.payloads.pop(path, None)

    def _create_fork(self, source, provider, cwd):
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

    def fork(self, thread_id, provider):
        session = self._sessions[thread_id]
        return self._create_fork(
            self.payloads[session.rollout_path], provider, session.cwd
        )

    def fork_from_path(self, path, provider, cwd):
        return self._create_fork(self.payloads[path], provider, cwd)

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

    def test_local_workspace_updates_reuse_remote_snapshot(self):
        status = {"provider_details": [], "providers": []}
        self.fleet.workspace(status, [], wait_for_remote=True)
        reads = (self.source.session_reads, self.target.session_reads)

        snapshot = self.fleet.workspace({**status, "session_count": 99}, [])

        self.assertEqual((self.source.session_reads, self.target.session_reads), reads)
        self.assertIn("session-1", {item["id"] for item in snapshot["sessions"]})

    def test_failed_refresh_preserves_last_successful_snapshot(self):
        status = {"provider_details": [], "providers": []}
        self.fleet.workspace(status, [], wait_for_remote=True)
        self.source.failure = "temporary failure"

        self.fleet._scan_remote_batch(self.fleet._refresh_hosts(), {"source-host"})
        snapshot = self.fleet.host_snapshot()
        source = next(host for host in snapshot["hosts"] if host["id"] == "source-host")

        self.assertTrue(snapshot["ready"])
        self.assertEqual(source["error"], "temporary failure")
        self.assertIn("session-1", {item["id"] for item in snapshot["sessions"]})

    def test_remote_timeout_finishes_loading_state(self):
        release = threading.Event()
        original = self.source.sessions

        def delayed_sessions():
            release.wait(0.2)
            return original()

        self.source.sessions = delayed_sessions
        self.fleet._remote_scan_timeout = 0.01
        started = time.monotonic()
        self.fleet.workspace({"provider_details": [], "providers": []}, [], wait_for_remote=True)
        elapsed = time.monotonic() - started
        release.set()

        snapshot = self.fleet.host_snapshot()
        source = next(host for host in snapshot["hosts"] if host["id"] == "source-host")
        self.assertLess(elapsed, 0.1)
        self.assertTrue(snapshot["ready"])
        self.assertFalse(source["connected"])
        self.assertIn("exceeded", source["error"])

    def test_background_scan_notifies_without_frontend_polling(self):
        condition = threading.Condition()
        notifications = 0

        def notify():
            nonlocal notifications
            with condition:
                notifications += 1
                condition.notify_all()

        self.fleet.set_change_notifier(notify)
        self.fleet.workspace({"provider_details": [], "providers": []}, [])
        with condition:
            self.assertTrue(condition.wait_for(lambda: notifications >= 2, timeout=1))
        self.assertTrue(self.fleet.host_snapshot()["ready"])

    def test_failed_background_scan_retries_once(self):
        attempts = 0
        original = self.source.sessions

        def transient_sessions():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary failure")
            return original()

        self.source.sessions = transient_sessions
        condition = threading.Condition()
        notifications = 0

        def notify():
            nonlocal notifications
            with condition:
                notifications += 1
                condition.notify_all()

        self.fleet.set_change_notifier(notify)
        self.fleet.workspace({"provider_details": [], "providers": []}, [])
        with condition:
            self.assertTrue(condition.wait_for(lambda: attempts >= 2, timeout=3))
        source = next(
            host
            for host in self.fleet.host_snapshot()["hosts"]
            if host["id"] == "source-host"
        )
        self.assertEqual(attempts, 2)
        self.assertTrue(source["connected"])
        self.assertIsNone(source["error"])

    def test_ssh_provider_config_is_cached(self):
        adapter = SshHostAdapter("test-host")
        adapter._codex_home = "/tmp/.codex"
        calls = 0

        class AppServer:
            def request(self, method, params):
                nonlocal calls
                calls += 1
                return {"config": {"model_providers": {"custom": {}}}}

        adapter.app_server = AppServer()
        adapter.provider_details([])
        adapter.provider_details([])
        self.assertEqual(adapter.state_db_path, "/tmp/.codex/state_5.sqlite")
        self.assertEqual(calls, 1)

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

    def test_same_remote_host_fork_uses_direct_fork(self):
        plan = self.fleet.preview_transfer(
            ["session-1"], "source-host", "source-host", "target", "", False
        )
        self.assertTrue(plan["executable"])
        self.assertFalse(
            any(risk["code"] == "experimental-path-import" for risk in plan["risks"])
        )

        result = self.fleet.transfer_batch(
            ["session-1"], "source-host", "source-host", "target", "", False, "FORK"
        )

        self.assertIsNone(result["failed"])
        self.assertFalse(self.source.sessions()[0].archived)
        forked = next(item for item in self.source.sessions() if item.id != "session-1")
        self.assertEqual(forked.provider, "target")
        self.assertEqual(result["completed"][0]["kind"], "same_host_fork")

    def test_same_remote_host_move_archives_source_and_restores(self):
        result = self.fleet.transfer_batch(
            ["session-1"], "source-host", "source-host", "target", "", True, "MIGRATE"
        )
        operation = result["completed"][0]
        self.assertEqual(operation["kind"], "same_host_move")
        self.assertTrue(
            next(item for item in self.source.sessions() if item.id == "session-1").archived
        )
        self.assertTrue(self.fleet.preview_restore(operation["operation_id"])["executable"])

        restored = self.fleet.restore(operation["operation_id"], "RESTORE")

        self.assertEqual(restored["kind"], "same_host_restore")
        self.assertFalse(self.source.sessions()[0].archived)
        self.assertEqual([item.id for item in self.source.sessions()], ["session-1"])

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
