from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_transfer.change_monitor import WorkspaceChangeMonitor


class WorkspaceChangeMonitorTest(unittest.TestCase):
    def test_wait_coalesces_changes_and_workspace_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            monitor = WorkspaceChangeMonitor(Path(root))
            monitor.publish("locks")
            latest = monitor.publish("workspace")
            change = monitor.wait(0, timeout=0)
            self.assertIsNotNone(change)
            self.assertEqual(change.revision, latest.revision)
            self.assertEqual(change.kind, "workspace")

    def test_wait_times_out_without_polling_or_changes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            monitor = WorkspaceChangeMonitor(Path(root))
            self.assertIsNone(monitor.wait(0, timeout=0.001))

    def test_unknown_change_kind_is_conservatively_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            monitor = WorkspaceChangeMonitor(Path(root))
            change = monitor.publish("unexpected")
            self.assertEqual(change.kind, "workspace")

    def test_workspace_event_is_filtered_when_visible_state_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            value = ["same"]
            monitor = WorkspaceChangeMonitor(Path(root), lambda: tuple(value))
            monitor._last_workspace_fingerprint = tuple(value)
            monitor._publish_workspace_if_changed()
            self.assertEqual(monitor.revision, 0)
            value.append("renamed")
            monitor._publish_workspace_if_changed()
            self.assertEqual(monitor.revision, 1)


if __name__ == "__main__":
    unittest.main()
