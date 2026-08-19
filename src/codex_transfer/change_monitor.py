from __future__ import annotations

import os
import select
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Hashable


@dataclass(frozen=True)
class WorkspaceChange:
    revision: int
    kind: str


class WorkspaceChangeMonitor:
    """Coalesce native Codex-home events behind one revisioned interface."""

    def __init__(
        self,
        codex_home: Path,
        workspace_fingerprint: Callable[[], Hashable] | None = None,
    ):
        self.codex_home = codex_home
        self._workspace_fingerprint = workspace_fingerprint
        self._last_workspace_fingerprint: Hashable | None = None
        self._condition = threading.Condition()
        self._history: deque[WorkspaceChange] = deque(maxlen=256)
        self._revision = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.native = hasattr(select, "kqueue") and hasattr(select, "kevent")

    def start(self) -> None:
        if not self.native or self._thread is not None:
            return
        self._last_workspace_fingerprint = self._read_workspace_fingerprint()
        self._thread = threading.Thread(
            target=self._run_kqueue,
            name="codex-transfer-change-monitor",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def publish(self, kind: str = "workspace") -> WorkspaceChange:
        if kind not in {"locks", "workspace"}:
            kind = "workspace"
        with self._condition:
            self._revision += 1
            change = WorkspaceChange(self._revision, kind)
            self._history.append(change)
            self._condition.notify_all()
            return change

    @property
    def revision(self) -> int:
        with self._condition:
            return self._revision

    def wait(self, after_revision: int, timeout: float = 20.0) -> WorkspaceChange | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._revision > after_revision or self._stop.is_set(),
                timeout=timeout,
            )
            if self._revision <= after_revision:
                return None
            changes = [item for item in self._history if item.revision > after_revision]
            kind = "workspace" if not changes or any(item.kind == "workspace" for item in changes) else "locks"
            return WorkspaceChange(self._revision, kind)

    def _watch_targets(self) -> dict[Path, str]:
        targets: dict[Path, str] = {}
        if self.codex_home.exists():
            targets[self.codex_home] = "workspace"
        lock_dir = self.codex_home / "thread-writer-locks"
        if lock_dir.exists():
            targets[lock_dir] = "locks"
        for name in ("config.toml", "session_index.jsonl"):
            path = self.codex_home / name
            if path.exists():
                targets[path] = "workspace"
        # SQLite read transactions can update shared-memory lock bytes. Watching
        # `-shm` or access attributes would turn our own refresh into a loop.
        for pattern in ("state*.sqlite", "state*.sqlite-wal"):
            for path in self.codex_home.glob(pattern):
                targets[path] = "workspace"
        for name in ("sessions", "archived_sessions"):
            path = self.codex_home / name
            if path.exists():
                targets[path] = "workspace"
        return targets

    def _read_workspace_fingerprint(self) -> Hashable | None:
        if self._workspace_fingerprint is None:
            return None
        try:
            return self._workspace_fingerprint()
        except Exception:
            return None

    def _publish_workspace_if_changed(self) -> None:
        current = self._read_workspace_fingerprint()
        if self._workspace_fingerprint is None or current is None:
            self.publish("workspace")
            return
        if current != self._last_workspace_fingerprint:
            self._last_workspace_fingerprint = current
            self.publish("workspace")

    def _run_kqueue(self) -> None:
        queue = select.kqueue()
        descriptors: dict[int, tuple[Path, str]] = {}
        workspace_dirty_at: float | None = None

        def rebuild() -> None:
            nonlocal descriptors
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            descriptors = {}
            for path, kind in self._watch_targets().items():
                try:
                    descriptor = os.open(path, getattr(os, "O_EVTONLY", os.O_RDONLY))
                    event = select.kevent(
                        descriptor,
                        filter=select.KQ_FILTER_VNODE,
                        flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                        fflags=(
                            select.KQ_NOTE_WRITE
                            | select.KQ_NOTE_EXTEND
                            | select.KQ_NOTE_DELETE
                            | select.KQ_NOTE_RENAME
                            | select.KQ_NOTE_REVOKE
                        ),
                    )
                    queue.control([event], 0, 0)
                    descriptors[descriptor] = (path, kind)
                except OSError:
                    if "descriptor" in locals():
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass

        try:
            rebuild()
            while not self._stop.is_set():
                now = time.monotonic()
                timeout = 1.0
                if workspace_dirty_at is not None:
                    timeout = max(0.0, min(timeout, workspace_dirty_at - now))
                events = queue.control(None, 64, timeout)
                rebuild_needed = False
                for event in events:
                    watched = descriptors.get(event.ident)
                    if watched is None:
                        continue
                    path, kind = watched
                    if kind == "locks":
                        self.publish("locks")
                    else:
                        observed_at = time.monotonic()
                        workspace_dirty_at = observed_at + 0.8
                    if path.is_dir() or event.fflags & (
                        select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME | select.KQ_NOTE_REVOKE
                    ):
                        rebuild_needed = True
                if rebuild_needed:
                    rebuild()
                if workspace_dirty_at is not None and time.monotonic() >= workspace_dirty_at:
                    self._publish_workspace_if_changed()
                    workspace_dirty_at = None
        finally:
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            queue.close()
