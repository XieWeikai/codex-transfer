from __future__ import annotations

import contextlib
import hashlib
import json
import os
import posixpath
import re
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .app_server import AppServerError, CodexAppServer, ForkResult
from .audit import AuditStore
from .model import Risk, Session, TraceProfile, require_safe_identifier
from .providers import provider_catalog
from .repository import CodexRepository

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


class FleetError(RuntimeError):
    pass


@dataclass(frozen=True)
class HostDescriptor:
    id: str
    label: str
    kind: str
    connected: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HostAdapter(Protocol):
    descriptor: HostDescriptor

    def sessions(self) -> list[Session]: ...

    def provider_ids(self) -> list[str]: ...

    def provider_details(self, sessions: list[Session]) -> list[dict[str, Any]]: ...

    def fetch_rollout(self, path: str) -> bytes: ...

    def stage_rollout(self, operation_id: str, session_id: str, payload: bytes) -> str: ...

    def remove_staged_rollout(self, path: str) -> None: ...

    def fork_from_path(self, path: str, provider: str, cwd: str) -> ForkResult: ...

    def set_archived(self, thread_id: str, archived: bool) -> None: ...

    def delete_thread(self, thread_id: str) -> None: ...

    def cwd_exists(self, path: str) -> bool: ...

    def integrity_check(self) -> str: ...

    def backup_databases(
        self, audit: AuditStore, operation_dir: Path, start_index: int
    ) -> list[dict[str, Any]]: ...


class DesktopSshDiscovery:
    """Find only SSH proxy processes owned by the running Codex desktop app."""

    _ALIAS_BEFORE_WRAPPER = re.compile(r"\s([A-Za-z0-9_.@-]+)\s+sh\s+-c\s")

    def aliases(self) -> list[str]:
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,command="],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        processes: dict[int, tuple[int, str]] = {}
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) != 3:
                continue
            with contextlib.suppress(ValueError):
                processes[int(parts[0])] = (int(parts[1]), parts[2])

        aliases = set()
        for _pid, (parent_id, command) in processes.items():
            if "codex app-server proxy" not in command:
                continue
            try:
                executable = os.path.basename(shlex.split(command)[0])
            except (ValueError, IndexError):
                continue
            if executable != "ssh":
                continue
            parent = processes.get(parent_id, (0, ""))[1]
            if not any(marker in parent for marker in ("/Codex.app/", "/ChatGPT.app/")):
                continue
            match = self._ALIAS_BEFORE_WRAPPER.search(command)
            if match:
                aliases.add(match.group(1))
        return sorted(aliases, key=str.casefold)


class LocalHostAdapter:
    def __init__(self, repository: CodexRepository, app_server: CodexAppServer):
        self.repository = repository
        self.app_server = app_server
        self.descriptor = HostDescriptor("local", "This Mac", "local")
        self._staging_root = repository.home / "imports" / "codex-transfer"

    def sessions(self) -> list[Session]:
        return self.repository.scan_sessions()

    def provider_ids(self) -> list[str]:
        return self.repository.provider_ids()

    def provider_details(self, sessions: list[Session]) -> list[dict[str, Any]]:
        return self.repository.provider_details(sessions)

    def fetch_rollout(self, path: str) -> bytes:
        validated = self.repository._validated_rollout_path(path)
        return validated.read_bytes()

    def stage_rollout(self, operation_id: str, session_id: str, payload: bytes) -> str:
        operation_id = require_safe_identifier(operation_id, "operation ID")
        session_id = require_safe_identifier(session_id, "session ID")
        self._staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self._staging_root / f"{operation_id}-{session_id}.jsonl"
        temp = destination.with_suffix(".tmp")
        temp.write_bytes(payload)
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
        return str(destination)

    def remove_staged_rollout(self, path: str) -> None:
        target = Path(path).resolve()
        if not target.is_relative_to(self._staging_root.resolve()):
            raise FleetError("Refusing to remove a file outside the local import staging area")
        target.unlink(missing_ok=True)

    def fork_from_path(self, path: str, provider: str, cwd: str) -> ForkResult:
        return self.app_server.fork_from_path(path, provider, cwd)

    def set_archived(self, thread_id: str, archived: bool) -> None:
        self.app_server.set_archived(thread_id, archived)

    def delete_thread(self, thread_id: str) -> None:
        self.app_server.delete(thread_id)

    def cwd_exists(self, path: str) -> bool:
        return Path(path).expanduser().is_dir()

    def integrity_check(self) -> str:
        results = [self.repository.integrity_check(path) for path in self.repository.state_db_paths()]
        return "ok" if results and all(result == "ok" for result in results) else ", ".join(results)

    def backup_databases(
        self, audit: AuditStore, operation_dir: Path, start_index: int
    ) -> list[dict[str, Any]]:
        entries = []
        for offset, path in enumerate(self.repository.state_db_paths()):
            entry = audit.backup_database(operation_dir, path, start_index + offset)
            entry["host_id"] = self.descriptor.id
            entries.append(entry)
        if not entries:
            raise FleetError("Local Codex state database is missing")
        return entries


class SshHostAdapter:
    _REMOTE_WRAPPER = r'''if [ -z "$SHELL" ] || [ ! -x "$SHELL" ]; then exit 127; fi
CODEX_REMOTE_PAYLOAD="$1"; export CODEX_REMOTE_PAYLOAD
case "${SHELL##*/}" in
  csh|tcsh) exec "$SHELL" -i -c 'exec /bin/sh -c "$CODEX_REMOTE_PAYLOAD"' ;;
  nu) exec "$SHELL" -l -i -c 'exec /bin/sh -c $env.CODEX_REMOTE_PAYLOAD' ;;
  fish|xonsh) exec "$SHELL" -l -i -c 'exec /bin/sh -c "$CODEX_REMOTE_PAYLOAD"' ;;
  *) exec "$SHELL" -l -i -c 'CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"; export CODEX_HOME; exec /bin/sh -c "$CODEX_REMOTE_PAYLOAD"' ;;
esac'''

    _SNAPSHOT_SCRIPT = """import os,sqlite3,sys,tempfile
source=sys.argv[1]
fd,target=tempfile.mkstemp(suffix='.sqlite'); os.close(fd)
try:
    with sqlite3.connect('file:'+source+'?mode=ro',uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    with open(target,'rb') as handle:
        shutil=getattr(sys.stdout,'buffer',sys.stdout); shutil.write(handle.read())
finally:
    os.unlink(target)
"""
    _LOCK_SCRIPT = """import fcntl,json,os,sys
root=sys.argv[1]
locked=[]
for thread_id in json.load(sys.stdin):
    path=os.path.join(root,'thread-writer-locks',thread_id+'.lock')
    if not os.path.exists(path): continue
    try:
        with open(path,'a+b') as handle:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
    except (OSError,BlockingIOError): locked.append(thread_id)
json.dump(locked,sys.stdout)
"""

    def __init__(self, alias: str, timeout: float = 20.0):
        self.alias = require_safe_identifier(alias, "SSH host")
        self.timeout = timeout
        self.descriptor = HostDescriptor(alias, alias, "ssh")
        # Discovery identifies hosts from Desktop's proxy processes, while each
        # operation uses an isolated app-server so it cannot disturb that proxy.
        payload = 'PATH="${CODEX_INSTALL_DIR:-$HOME/.local/bin}:$HOME/bin:$PATH"; export PATH; exec codex app-server --stdio'
        remote_command = "sh -c " + shlex.quote(self._REMOTE_WRAPPER) + " sh " + shlex.quote(payload)
        self.app_server = CodexAppServer(
            Path("/"),
            timeout=timeout,
            command=self._ssh_command(remote_command, tty=True),
        )
        self._codex_home: str | None = None
        self._state_db_path: str | None = None

    def _ssh_command(self, remote_command: str, tty: bool = False) -> list[str]:
        command = [
            "ssh",
            "-T" if tty else "-o",
        ]
        if not tty:
            command.append("BatchMode=yes")
        else:
            command.extend(["-o", "BatchMode=yes"])
        command.extend(
            [
                "-o",
                "ConnectTimeout=6",
                "-o",
                "ServerAliveInterval=15",
                "-o",
                "ServerAliveCountMax=2",
                self.alias,
                remote_command,
            ]
        )
        return command

    def _run(self, remote_command: str, payload: bytes | None = None) -> bytes:
        try:
            result = subprocess.run(
                self._ssh_command(remote_command),
                input=payload,
                capture_output=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FleetError(f"SSH host {self.alias} is unavailable: {exc}") from exc
        if result.returncode:
            detail = result.stderr.decode("utf-8", "replace").strip()[-500:]
            raise FleetError(f"SSH host {self.alias} rejected the operation: {detail}")
        return result.stdout

    @property
    def codex_home(self) -> str:
        if self._codex_home is None:
            value = self._run("printf '%s' \"${CODEX_HOME:-$HOME/.codex}\"").decode(
                "utf-8", "strict"
            )
            if not value.startswith("/"):
                raise FleetError(f"Codex on {self.alias} returned an invalid home directory")
            self._codex_home = posixpath.normpath(value)
        return self._codex_home

    def _validate_path(self, path: str, staging_only: bool = False) -> str:
        normalized = posixpath.normpath(path)
        root = posixpath.join(self.codex_home, "imports", "codex-transfer") if staging_only else self.codex_home
        if normalized != root and not normalized.startswith(root.rstrip("/") + "/"):
            raise FleetError(f"Remote path escapes Codex home on {self.alias}")
        return normalized

    @property
    def state_db_path(self) -> str:
        if self._state_db_path is None:
            config = self.app_server.request("config/read", {}).get("config") or {}
            sqlite_home = config.get("sqlite_home") or config.get("sqliteHome")
            if isinstance(sqlite_home, str) and sqlite_home.startswith("/"):
                self._state_db_path = posixpath.join(sqlite_home, "state_5.sqlite")
            else:
                self._state_db_path = posixpath.join(self.codex_home, "state_5.sqlite")
        return self._state_db_path

    def sessions(self) -> list[Session]:
        result = []
        threads = self.app_server.list_threads()
        thread_ids = [thread.get("id") for thread in threads if isinstance(thread.get("id"), str)]
        command = (
            "python3 -c "
            + shlex.quote(self._LOCK_SCRIPT)
            + " "
            + shlex.quote(self.codex_home)
        )
        locked_ids = set(
            json.loads(self._run(command, json.dumps(thread_ids).encode("utf-8")) or b"[]")
        )
        for thread in threads:
            path = thread.get("path")
            session_id = thread.get("id")
            if not isinstance(path, str) or not isinstance(session_id, str):
                continue
            with contextlib.suppress(FleetError):
                path = self._validate_path(path)
                status = thread.get("status") or {}
                provider = str(thread.get("modelProvider") or "openai")
                result.append(
                    Session(
                        id=session_id,
                        title=str(thread.get("name") or thread.get("preview") or "Untitled session"),
                        provider=provider,
                        model=str(thread["model"]) if thread.get("model") else None,
                        cwd=str(thread.get("cwd") or ""),
                        updated_at=int(thread.get("updatedAt") or 0),
                        rollout_path=path,
                        db_path=self.state_db_path,
                        archived=bool(thread.get("archived")),
                        locked=session_id in locked_ids or status.get("type") == "active",
                        rollout_provider=provider,
                        size_bytes=0,
                        host_id=self.alias,
                    )
                )
        return sorted(result, key=lambda item: item.updated_at, reverse=True)

    def provider_ids(self) -> list[str]:
        return self.app_server.provider_ids()

    def provider_details(self, sessions: list[Session]) -> list[dict[str, Any]]:
        result = self.app_server.request("config/read", {})
        config = result.get("config") or {}
        return provider_catalog(
            config if isinstance(config, dict) else {},
            sessions,
            host_id=self.alias,
            config_source=posixpath.join(self.codex_home, "config.toml"),
        )

    def fetch_rollout(self, path: str) -> bytes:
        path = self._validate_path(path)
        return self._run("cat -- " + shlex.quote(path))

    def stage_rollout(self, operation_id: str, session_id: str, payload: bytes) -> str:
        operation_id = require_safe_identifier(operation_id, "operation ID")
        session_id = require_safe_identifier(session_id, "session ID")
        directory = posixpath.join(self.codex_home, "imports", "codex-transfer")
        destination = posixpath.join(directory, f"{operation_id}-{session_id}.jsonl")
        command = (
            "umask 077; mkdir -p "
            + shlex.quote(directory)
            + "; cat > "
            + shlex.quote(destination)
        )
        self._run(command, payload)
        return destination

    def remove_staged_rollout(self, path: str) -> None:
        path = self._validate_path(path, staging_only=True)
        self._run("rm -f -- " + shlex.quote(path))

    def fork_from_path(self, path: str, provider: str, cwd: str) -> ForkResult:
        return self.app_server.fork_from_path(self._validate_path(path), provider, cwd)

    def set_archived(self, thread_id: str, archived: bool) -> None:
        self.app_server.set_archived(thread_id, archived)

    def delete_thread(self, thread_id: str) -> None:
        self.app_server.delete(thread_id)

    def cwd_exists(self, path: str) -> bool:
        result = subprocess.run(
            self._ssh_command("test -d " + shlex.quote(path)),
            capture_output=True,
            timeout=self.timeout,
        )
        return result.returncode == 0

    def integrity_check(self) -> str:
        script = "import sqlite3,sys; print(sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True).execute('pragma integrity_check').fetchone()[0])"
        output = self._run("python3 -c " + shlex.quote(script) + " " + shlex.quote(self.state_db_path))
        return output.decode("utf-8", "replace").strip()

    def backup_databases(
        self, audit: AuditStore, operation_dir: Path, start_index: int
    ) -> list[dict[str, Any]]:
        db_path = self.state_db_path
        command = "python3 -c " + shlex.quote(self._SNAPSHOT_SCRIPT) + " " + shlex.quote(db_path)
        payload = self._run(command)
        if not payload.startswith(b"SQLite format 3\x00"):
            raise FleetError(f"Remote database snapshot from {self.alias} is invalid")
        return [
            audit.store_database_snapshot(
                operation_dir, payload, start_index, db_path, self.descriptor.id
            )
        ]


def inspect_rollout(payload: bytes, session_id: str) -> tuple[str | None, TraceProfile]:
    provider = None
    parsed = malformed = encrypted = 0
    for raw_line in payload.splitlines():
        try:
            value = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            malformed += 1
            continue
        parsed += 1
        if value.get("type") == "session_meta":
            meta = value.get("payload") or {}
            if meta.get("id") == session_id and meta.get("model_provider") is not None:
                provider = str(meta["model_provider"])
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                encrypted += int(bool(current.get("encrypted_content")))
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return provider, TraceProfile(session_id, parsed, malformed, encrypted)


class HostFleet:
    """Deep module for discovering hosts and executing reversible cross-host transfers."""

    def __init__(
        self,
        local_repository: CodexRepository,
        audit: AuditStore,
        local_app_server: CodexAppServer,
        discovery: DesktopSshDiscovery | None = None,
        adapters: dict[str, HostAdapter] | None = None,
    ):
        self.audit = audit
        self.discovery = discovery or DesktopSshDiscovery()
        self.local = LocalHostAdapter(local_repository, local_app_server)
        self._injected_adapters = adapters
        self._adapters: dict[str, HostAdapter] = {"local": self.local}
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._cache_lock = threading.Lock()
        self._scan_thread: threading.Thread | None = None
        self.lock_path = audit.root / "fleet.lock"

    def _refresh_hosts(self) -> dict[str, HostAdapter]:
        if self._injected_adapters is not None:
            self._adapters = dict(self._injected_adapters)
            return self._adapters
        active = set(self.discovery.aliases())
        for alias in active:
            self._adapters.setdefault(alias, SshHostAdapter(alias))
        self._adapters = {
            host_id: adapter
            for host_id, adapter in self._adapters.items()
            if host_id == "local" or host_id in active
        }
        return self._adapters

    def _host(self, host_id: str) -> HostAdapter:
        host_id = require_safe_identifier(host_id, "host")
        adapters = self._refresh_hosts()
        if host_id not in adapters:
            raise FleetError(f"Host {host_id!r} is not an active Codex Desktop connection")
        return adapters[host_id]

    def host_snapshot(self) -> dict[str, Any]:
        with self._cache_lock:
            if self._cache is not None:
                return {
                    "ready": True,
                    "hosts": self._cache[1]["hosts"],
                    "sessions": [
                        item
                        for item in self._cache[1]["sessions"]
                        if item.get("host_id") != "local"
                    ],
                }
        hosts = []
        for adapter in self._refresh_hosts().values():
            descriptor = adapter.descriptor.to_dict()
            descriptor.update(
                {"providers": [], "provider_details": [], "session_count": 0, "loading": True}
            )
            hosts.append(descriptor)
        hosts.sort(key=lambda item: (item["id"] != "local", item["label"].casefold()))
        return {"ready": False, "hosts": hosts, "sessions": []}

    def workspace(
        self,
        local_status: dict[str, Any],
        operations: list[dict],
        local_sessions: list[Session] | None = None,
        wait_for_remote: bool = False,
    ) -> dict[str, Any]:
        with self._cache_lock:
            if not wait_for_remote and self._cache and time.monotonic() - self._cache[0] < 15:
                cached = dict(self._cache[1])
                cached["operations"] = operations
                if local_sessions is not None:
                    cached["sessions"] = [
                        *[item for item in cached["sessions"] if item.get("host_id") != "local"],
                        *(session.to_summary_dict() for session in local_sessions),
                    ]
                    cached["sessions"].sort(
                        key=lambda item: item.get("updated_at", 0), reverse=True
                    )
                return cached
        if wait_for_remote:
            return self._scan_workspace(local_status, operations, local_sessions)

        adapters = self._refresh_hosts()
        with self._cache_lock:
            if self._scan_thread is None or not self._scan_thread.is_alive():
                self._cache = None
                self._scan_thread = threading.Thread(
                    target=self._scan_workspace,
                    args=(dict(local_status), list(operations), local_sessions),
                    name="codex-transfer-host-scan",
                    daemon=True,
                )
                self._scan_thread.start()
        local_items = local_sessions or self.local.sessions()
        hosts = []
        for adapter in adapters.values():
            descriptor = adapter.descriptor.to_dict()
            if adapter is self.local:
                provider_details = local_status.get("provider_details") or self.local.provider_details(
                    local_items
                )
                descriptor.update(
                    {
                        "providers": [item["id"] for item in provider_details],
                        "provider_details": provider_details,
                        "session_count": len(local_items),
                        "loading": False,
                    }
                )
            else:
                descriptor.update(
                    {"providers": [], "provider_details": [], "session_count": 0, "loading": True}
                )
            hosts.append(descriptor)
        hosts.sort(key=lambda item: (item["id"] != "local", item["label"].casefold()))
        return {
            "status": {**local_status, "host_count": len(hosts)},
            "hosts": hosts,
            "sessions": [session.to_summary_dict() for session in local_items],
            "operations": operations,
        }

    def _scan_workspace(
        self,
        local_status: dict[str, Any],
        operations: list[dict],
        local_sessions: list[Session] | None,
    ) -> dict[str, Any]:
        adapters = self._refresh_hosts()
        sessions: list[Session] = []
        hosts: list[dict[str, Any]] = []

        def scan(adapter: HostAdapter) -> tuple[list[Session], list[dict[str, Any]]]:
            if adapter is self.local and local_sessions is not None:
                items = local_sessions
                details = local_status.get("provider_details") or adapter.provider_details(items)
            else:
                items = adapter.sessions()
                details = adapter.provider_details(items)
            return items, details

        with ThreadPoolExecutor(max_workers=min(6, len(adapters))) as executor:
            jobs = {executor.submit(scan, adapter): adapter for adapter in adapters.values()}
            for future in as_completed(jobs):
                adapter = jobs[future]
                descriptor = adapter.descriptor.to_dict()
                try:
                    items, provider_details = future.result()
                    sessions.extend(items)
                    descriptor["provider_details"] = provider_details
                    descriptor["providers"] = [item["id"] for item in provider_details]
                    descriptor["session_count"] = len(items)
                except Exception as exc:
                    descriptor.update(
                        {
                            "connected": False,
                            "error": str(exc),
                            "providers": [],
                            "provider_details": [],
                            "session_count": 0,
                        }
                    )
                hosts.append(descriptor)
        hosts.sort(key=lambda item: (item["id"] != "local", item["label"].casefold()))
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        result = {
            "status": {**local_status, "host_count": len(hosts)},
            "hosts": hosts,
            "sessions": [session.to_summary_dict() for session in sessions],
            "operations": operations,
        }
        with self._cache_lock:
            self._cache = (time.monotonic(), result)
        return result

    def preview_archive(
        self, session_ids: list[str], host_id: str, archived: bool
    ) -> dict[str, Any]:
        if not isinstance(archived, bool):
            raise FleetError("archived must be a JSON boolean")
        if not session_ids:
            raise FleetError("Select at least one session")
        host = self._host(host_id)
        indexed = {session.id: session for session in host.sessions()}
        unique_ids = list(dict.fromkeys(session_ids))
        missing = [session_id for session_id in unique_ids if session_id not in indexed]
        if missing:
            raise FleetError("Unknown sessions on host " + host_id + ": " + ", ".join(missing))
        sessions = [indexed[session_id] for session_id in unique_ids]
        risks: list[Risk] = []
        estimated = 0

        for session in sessions:
            if session.archived == archived:
                risks.append(
                    Risk(
                        "critical",
                        "archive-state-changed",
                        f"会话 {session.id} 已经是{'已归档' if archived else '未归档'}状态。",
                        "刷新列表，只选择状态与本次操作匹配的会话。",
                    )
                )
            if session.locked:
                risks.append(
                    Risk(
                        "critical",
                        "session-active",
                        f"会话 {session.id} 的独占 writer lock 正被主机 {host_id} 上的 Codex 持有。",
                        "关闭或停止该远程 Codex 任务，然后重新运行预检。",
                    )
                )
            try:
                estimated += len(host.fetch_rollout(session.rollout_path))
            except Exception as exc:
                risks.append(
                    Risk(
                        "critical",
                        "rollout-missing",
                        f"主机 {host_id} 上会话 {session.id} 的 rollout 无法读取：{exc}",
                        "先在远程主机修复文件或从可信备份恢复，再重试。",
                    )
                )

        integrity = host.integrity_check()
        if integrity != "ok":
            risks.append(
                Risk(
                    "critical",
                    "database-integrity",
                    f"主机 {host_id} 的 Codex SQLite 完整性检查失败：{integrity}",
                    "修复远程数据库后再改变归档状态。",
                )
            )
        risks.append(
            Risk(
                "warning" if archived else "info",
                "archive-hides-session" if archived else "unarchive-preserves-session",
                "归档会让 Session 从远程 Codex 的默认活动列表隐藏，但不会删除聊天记录。"
                if archived
                else "还原归档只恢复远程 Session 的可见状态，不会改变 Provider 或聊天内容。",
                "需要继续使用时可在同一张卡片上还原归档；操作前会保存远程 rollout 和数据库快照。"
                if archived
                else "操作后刷新 Codex Desktop；若仍未显示，请检查远程 Project 筛选。",
            )
        )
        if len(sessions) > 1:
            risks.append(
                Risk(
                    "warning",
                    "archive-batch-non-atomic",
                    "批量远程归档操作逐条执行，每条都有独立备份和审计记录，但整批不是原子事务。",
                    "中途失败时保留已完成条目，并按操作记录逐条核对。",
                )
            )
        return {
            "host_id": host_id,
            "archived": archived,
            "sessions": [session.to_summary_dict() for session in sessions],
            "risks": [risk.to_dict() for risk in risks],
            "estimated_backup_bytes": estimated,
            "executable": bool(sessions)
            and not any(risk.severity == "critical" for risk in risks),
        }

    def set_archived_batch(
        self,
        session_ids: list[str],
        host_id: str,
        archived: bool,
        acknowledgement: str,
    ) -> dict[str, Any]:
        expected = "ARCHIVE" if archived else "UNARCHIVE"
        if acknowledgement != expected:
            raise FleetError(f"Risk acknowledgement must equal {expected}")
        completed = []
        failed = None
        with self._exclusive_lock():
            plan = self.preview_archive(session_ids, host_id, archived)
            if not plan["executable"]:
                raise FleetError("Preflight has critical risks; remote archive batch was not started")
            for session in plan["sessions"]:
                session_id = session["id"]
                try:
                    completed.append(
                        self._set_archived_one(
                            session_id, host_id, archived, plan["risks"]
                        )
                    )
                except Exception as exc:
                    failed = {"session_id": session_id, "error": str(exc)}
                    break
        with self._cache_lock:
            self._cache = None
        return {
            "requested_session_ids": session_ids,
            "host_id": host_id,
            "archived": archived,
            "completed": completed,
            "failed": failed,
            "batch_atomic": False,
        }

    def _set_archived_one(
        self,
        session_id: str,
        host_id: str,
        archived: bool,
        risks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        host = self._host(host_id)
        source = {session.id: session for session in host.sessions()}.get(session_id)
        if source is None or source.locked or source.archived == archived:
            raise FleetError("Remote session changed after preflight")
        kind = "archive" if archived else "unarchive"
        operation_id, operation_dir = self.audit.new_operation(kind)
        manifest = {
            "operation_id": operation_id,
            "kind": kind,
            "status": "preparing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "host_id": host_id,
            "session_ids": [source.id],
            "archived_before": source.archived,
            "archived_after": archived,
            "files": [],
            "post_files": [],
            "databases": [],
            "risks": risks,
        }
        self.audit.write_manifest(operation_dir, manifest)
        changed = False
        try:
            payload = host.fetch_rollout(source.rollout_path)
            file_entry = self.audit.backup_bytes(
                operation_dir,
                payload,
                f"rollout/{host_id}-{source.id}.jsonl",
                f"{host_id}:{source.rollout_path}",
            )
            file_entry.update({"host_id": host_id, "session_id": source.id})
            manifest["files"].append(file_entry)
            manifest["databases"].extend(
                host.backup_databases(self.audit, operation_dir, 0)
            )
            manifest["status"] = "backed_up"
            self.audit.write_manifest(operation_dir, manifest)
            self.audit.append_event(operation_id, kind, "backed_up", {"host_id": host_id})

            host.set_archived(source.id, archived)
            changed = True
            refreshed = {session.id: session for session in host.sessions()}.get(source.id)
            if refreshed is None or refreshed.archived != archived:
                raise FleetError("Remote Codex did not apply the requested archive state")
            refreshed_payload = host.fetch_rollout(refreshed.rollout_path)
            manifest["post_files"] = [
                {
                    "host_id": host_id,
                    "session_id": refreshed.id,
                    "source": refreshed.rollout_path,
                    "after_sha256": hashlib.sha256(refreshed_payload).hexdigest(),
                    "size_bytes": len(refreshed_payload),
                }
            ]
            manifest["status"] = "completed"
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            self.audit.write_manifest(operation_dir, manifest)
            self.audit.append_event(
                operation_id,
                kind,
                "completed",
                {"host_id": host_id, "session": source.id, "archived": archived},
            )
            return manifest
        except Exception as exc:
            rollback_error = None
            with contextlib.suppress(Exception):
                current = {session.id: session for session in host.sessions()}.get(source.id)
                changed = current is not None and current.archived != source.archived
            if changed:
                try:
                    host.set_archived(source.id, source.archived)
                    rolled_back = {session.id: session for session in host.sessions()}.get(source.id)
                    if rolled_back is None or rolled_back.archived != source.archived:
                        raise FleetError("Remote Codex did not restore the original archive state")
                except Exception as rollback_exc:
                    rollback_error = str(rollback_exc)
            manifest["status"] = "rollback_failed" if rollback_error else "rolled_back"
            manifest["error"] = str(exc)
            if rollback_error:
                manifest["rollback_error"] = rollback_error
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            self.audit.write_manifest(operation_dir, manifest)
            self.audit.append_event(
                operation_id,
                kind,
                manifest["status"],
                {"host_id": host_id, "error": str(exc), "rollback_error": rollback_error},
            )
            if rollback_error:
                raise FleetError(
                    f"Remote {kind} failed and rollback also failed; use backup {operation_id}: "
                    f"{exc}; rollback: {rollback_error}"
                ) from exc
            raise FleetError(f"Remote {kind} failed and was rolled back: {exc}") from exc

    def preview_transfer(
        self,
        session_ids: list[str],
        source_host_id: str,
        target_host_id: str,
        target_provider: str,
        target_cwd: str,
        move: bool,
    ) -> dict[str, Any]:
        if not session_ids:
            raise FleetError("Select at least one session")
        if source_host_id == target_host_id:
            raise FleetError("Cross-host transfer requires two different hosts")
        target_provider = require_safe_identifier(target_provider, "target provider")
        if not target_cwd or not target_cwd.startswith("/"):
            raise FleetError("Target project path must be an absolute path")
        source = self._host(source_host_id)
        target = self._host(target_host_id)
        indexed = {session.id: session for session in source.sessions()}
        missing = [session_id for session_id in session_ids if session_id not in indexed]
        if missing:
            raise FleetError("Unknown source sessions: " + ", ".join(missing))
        sessions = [indexed[session_id] for session_id in dict.fromkeys(session_ids)]
        risks: list[Risk] = []
        profiles = []
        estimated = 0
        if not target.cwd_exists(target_cwd):
            risks.append(
                Risk(
                    "critical",
                    "target-project-missing",
                    f"目标主机 {target_host_id} 上不存在 Project 路径 {target_cwd}。",
                    "先在目标主机创建目录，或选择一个已存在的绝对路径。",
                )
            )
        if target_provider not in target.provider_ids():
            risks.append(
                Risk(
                    "warning",
                    "target-provider-not-configured",
                    f"目标主机没有报告 provider {target_provider!r}。",
                    "先在目标主机配置并验证该 provider；本工具不会复制凭据。",
                )
            )
        for session in sessions:
            if session.locked:
                risks.append(
                    Risk(
                        "critical",
                        "session-active",
                        f"会话 {session.id} 在 {source_host_id} 的 Codex 中仍处于加载状态。",
                        "关闭该远程任务，等它从 loaded 列表消失后重新预检。",
                    )
                )
                continue
            if session.archived:
                risks.append(
                    Risk(
                        "critical",
                        "source-archived",
                        f"会话 {session.id} 已归档。",
                        "先还原归档，再执行跨主机操作。",
                    )
                )
                continue
            payload = source.fetch_rollout(session.rollout_path)
            estimated += len(payload)
            provider, profile = inspect_rollout(payload, session.id)
            profiles.append(profile)
            if provider != session.provider:
                risks.append(
                    Risk(
                        "critical",
                        "metadata-mismatch",
                        f"会话 {session.id} 的索引 provider 与 rollout 不一致。",
                        "先在来源主机修复索引或从可信备份恢复。",
                    )
                )
            if profile.malformed_records:
                risks.append(
                    Risk(
                        "critical",
                        "trace-malformed",
                        f"会话 {session.id} 有 {profile.malformed_records} 条损坏记录。",
                        "不要传输损坏 trace；先恢复可信备份。",
                    )
                )
            if profile.encrypted_content_items:
                risks.append(
                    Risk(
                        "warning",
                        "encrypted-content-not-portable",
                        f"会话 {session.id} 包含 {profile.encrypted_content_items} 项 encrypted_content。",
                        "目标 provider 可能无法解密历史推理；优先 Fork 并保留来源。",
                    )
                )
        for adapter, label in ((source, "来源"), (target, "目标")):
            integrity = adapter.integrity_check()
            if integrity != "ok":
                risks.append(
                    Risk(
                        "critical",
                        "database-integrity",
                        f"{label}主机 Codex SQLite 完整性检查失败：{integrity}",
                        "修复数据库后再进行跨主机操作。",
                    )
                )
        risks.extend(
            [
                Risk(
                    "warning",
                    "experimental-path-import",
                    "跨主机导入依赖 Codex 实验性 thread/fork.path 接口，未来版本可能变化。",
                    "保持两端 Codex CLI 为兼容版本，并永久保留本次 rollout 与数据库快照。",
                ),
                Risk(
                    "warning",
                    "credentials-not-moved",
                    "Session 会传输，但 API Key、OAuth、Provider 配置和模型别名不会传输。",
                    "必须提前在目标主机独立配置并验证目标 provider。",
                ),
                Risk(
                    "warning" if move else "info",
                    "cross-host-move-archives-source" if move else "source-preserved",
                    "跨主机 Move 会在目标创建新 Session ID，验证成功后归档来源；不会删除来源。"
                    if move
                    else "跨主机 Fork 会创建新 Session ID，来源 Session 保持不变。",
                    "Move 可通过恢复操作删除未变化的目标副本并还原来源归档。"
                    if move
                    else "先验证目标副本能够恢复，再继续聊天。",
                ),
            ]
        )
        if len(sessions) > 1:
            risks.append(
                Risk(
                    "warning",
                    "cross-host-batch-non-atomic",
                    "批量跨主机操作逐条执行，整批不是原子事务。",
                    "中途失败时保留已完成条目，并按操作记录逐条恢复。",
                )
            )
        return {
            "source_host": source_host_id,
            "target_host": target_host_id,
            "target_provider": target_provider,
            "target_cwd": target_cwd,
            "move": move,
            "sessions": [session.to_summary_dict() for session in sessions],
            "trace_profiles": [profile.to_dict() for profile in profiles],
            "risks": [risk.to_dict() for risk in risks],
            "estimated_backup_bytes": estimated,
            "executable": bool(sessions) and not any(risk.severity == "critical" for risk in risks),
        }

    def transfer_batch(
        self,
        session_ids: list[str],
        source_host_id: str,
        target_host_id: str,
        target_provider: str,
        target_cwd: str,
        move: bool,
        acknowledgement: str,
    ) -> dict[str, Any]:
        expected = "MIGRATE" if move else "FORK"
        if acknowledgement != expected:
            raise FleetError(f"Risk acknowledgement must equal {expected}")
        completed = []
        failed = None
        with self._exclusive_lock():
            plan = self.preview_transfer(
                session_ids, source_host_id, target_host_id, target_provider, target_cwd, move
            )
            if not plan["executable"]:
                raise FleetError("Preflight has critical risks; cross-host transfer was not started")
            for session_id in session_ids:
                try:
                    completed.append(
                        self._transfer_one(
                            session_id,
                            source_host_id,
                            target_host_id,
                            target_provider,
                            target_cwd,
                            move,
                            plan["risks"],
                        )
                    )
                except Exception as exc:
                    failed = {"session_id": session_id, "error": str(exc)}
                    break
        self._cache = None
        return {
            "requested_session_ids": session_ids,
            "source_host": source_host_id,
            "target_host": target_host_id,
            "completed": completed,
            "failed": failed,
            "batch_atomic": False,
        }

    def _transfer_one(
        self,
        session_id: str,
        source_host_id: str,
        target_host_id: str,
        target_provider: str,
        target_cwd: str,
        move: bool,
        risks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        source = self._host(source_host_id)
        target = self._host(target_host_id)
        indexed = {session.id: session for session in source.sessions()}
        session = indexed.get(session_id)
        if session is None or session.locked or session.archived:
            raise FleetError("Source session changed after preflight")
        kind = "cross_host_move" if move else "cross_host_fork"
        operation_id, operation_dir = self.audit.new_operation(kind)
        manifest = {
            "operation_id": operation_id,
            "kind": kind,
            "status": "preparing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_host": source_host_id,
            "target_host": target_host_id,
            "source_provider": session.provider,
            "target_provider": target_provider,
            "target_cwd": target_cwd,
            "session_ids": [session.id],
            "forked_session_ids": [],
            "files": [],
            "created_files": [],
            "databases": [],
            "risks": risks,
            "source_archived": False,
        }
        self.audit.write_manifest(operation_dir, manifest)
        staged_path = None
        result: ForkResult | None = None
        source_archived = False
        try:
            payload = source.fetch_rollout(session.rollout_path)
            provider, profile = inspect_rollout(payload, session.id)
            if provider != session.provider or profile.malformed_records:
                raise FleetError("Source rollout changed or became invalid after preflight")
            file_entry = self.audit.backup_bytes(
                operation_dir,
                payload,
                f"source/{source_host_id}-{session.id}.jsonl",
                f"{source_host_id}:{session.rollout_path}",
            )
            file_entry.update({"host_id": source_host_id, "session_id": session.id})
            manifest["files"].append(file_entry)
            manifest["databases"].extend(source.backup_databases(self.audit, operation_dir, 0))
            manifest["databases"].extend(
                target.backup_databases(self.audit, operation_dir, len(manifest["databases"]))
            )
            manifest["status"] = "backed_up"
            self.audit.write_manifest(operation_dir, manifest)
            self.audit.append_event(operation_id, kind, "backed_up", {})

            staged_path = target.stage_rollout(operation_id, session.id, payload)
            if hashlib.sha256(target.fetch_rollout(staged_path)).hexdigest() != file_entry["before_sha256"]:
                raise FleetError("Target staging hash does not match the audited source backup")
            result = target.fork_from_path(staged_path, target_provider, target_cwd)
            target_sessions = {item.id: item for item in target.sessions()}
            created = target_sessions.get(result.thread_id)
            if created is None or created.provider != target_provider:
                raise FleetError("Target Codex did not index the imported Session under the requested provider")
            created_payload = target.fetch_rollout(created.rollout_path)
            created_provider, created_profile = inspect_rollout(created_payload, created.id)
            if created_provider != target_provider or created_profile.malformed_records:
                raise FleetError("Target Codex created an invalid imported rollout")
            manifest["forked_session_ids"] = [created.id]
            manifest["created_files"] = [
                {
                    "host_id": target_host_id,
                    "session_id": created.id,
                    "source": created.rollout_path,
                    "after_sha256": hashlib.sha256(created_payload).hexdigest(),
                    "size_bytes": len(created_payload),
                }
            ]
            if move:
                if hashlib.sha256(source.fetch_rollout(session.rollout_path)).hexdigest() != file_entry["before_sha256"]:
                    raise FleetError("Source Session changed during transfer; it was not archived")
                source.set_archived(session.id, True)
                source_archived = True
                manifest["source_archived"] = True
            manifest["status"] = "completed"
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            self.audit.write_manifest(operation_dir, manifest)
            self.audit.append_event(
                operation_id,
                kind,
                "completed",
                {"source": session.id, "target": result.thread_id},
            )
            return manifest
        except Exception as exc:
            rollback_errors = []
            if source_archived:
                try:
                    source.set_archived(session.id, False)
                except Exception as rollback_exc:
                    rollback_errors.append(f"source unarchive: {rollback_exc}")
            if result is not None:
                try:
                    target.delete_thread(result.thread_id)
                except Exception as rollback_exc:
                    rollback_errors.append(f"target delete: {rollback_exc}")
            manifest["status"] = "rollback_failed" if rollback_errors else "rolled_back"
            manifest["error"] = str(exc)
            if rollback_errors:
                manifest["rollback_errors"] = rollback_errors
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            self.audit.write_manifest(operation_dir, manifest)
            self.audit.append_event(
                operation_id, kind, manifest["status"], {"error": str(exc), "rollback_errors": rollback_errors}
            )
            detail = "; ".join(rollback_errors)
            if detail:
                raise FleetError(
                    f"Cross-host transfer failed and rollback was incomplete; use backup {operation_id}: {exc}; {detail}"
                ) from exc
            raise FleetError(f"Cross-host transfer failed and was rolled back: {exc}") from exc
        finally:
            if staged_path:
                with contextlib.suppress(Exception):
                    target.remove_staged_rollout(staged_path)

    def preview_restore(self, operation_id: str) -> dict[str, Any] | None:
        original = self.audit.read_manifest(operation_id)
        if original.get("kind") not in {"cross_host_fork", "cross_host_move"}:
            return None
        risks: list[Risk] = []
        if original.get("status") != "completed" or original.get("restored_by"):
            raise FleetError("Only an unrestored completed cross-host operation can be restored")
        source = self._host(original["source_host"])
        target = self._host(original["target_host"])
        target_id = original["forked_session_ids"][0]
        target_session = {item.id: item for item in target.sessions()}.get(target_id)
        if target_session is None:
            risks.append(Risk("critical", "target-missing", "目标 Session 已不存在。", "保留审计备份并手动检查目标主机。"))
        elif target_session.locked:
            risks.append(Risk("critical", "session-active", "目标 Session 正被 Codex 使用。", "关闭目标任务后重试。"))
        else:
            current_hash = hashlib.sha256(target.fetch_rollout(target_session.rollout_path)).hexdigest()
            if current_hash != original["created_files"][0]["after_sha256"]:
                risks.append(
                    Risk(
                        "critical",
                        "trace-diverged",
                        "目标 Session 已产生新聊天，恢复会删除这些内容，因此已阻止。",
                        "保留目标 Session，或先从它创建独立 Fork。",
                    )
                )
        source_id = original["session_ids"][0]
        source_session = {item.id: item for item in source.sessions()}.get(source_id)
        if source_session is None:
            risks.append(Risk("critical", "source-missing", "来源 Session 已不存在。", "使用审计备份手动恢复来源。"))
        elif original["kind"] == "cross_host_move" and not source_session.archived:
            risks.append(Risk("critical", "source-state-changed", "来源 Session 已不再归档。", "刷新状态并核对是否已经手动恢复。"))
        risks.append(
            Risk(
                "warning",
                "cross-host-target-removal",
                "恢复会删除目标主机上未变化的导入 Session；Move 还会还原来源归档。",
                "确认目标 Session 没有需要保留的新内容。",
            )
        )
        return {
            "operation_id": operation_id,
            "kind": original["kind"],
            "session_ids": [target_id],
            "risks": [risk.to_dict() for risk in risks],
            "executable": not any(risk.severity == "critical" for risk in risks),
        }

    def restore(self, operation_id: str, acknowledgement: str) -> dict[str, Any] | None:
        with self._exclusive_lock():
            return self._restore_unlocked(operation_id, acknowledgement)

    def _restore_unlocked(self, operation_id: str, acknowledgement: str) -> dict[str, Any] | None:
        original = self.audit.read_manifest(operation_id)
        if original.get("kind") not in {"cross_host_fork", "cross_host_move"}:
            return None
        if acknowledgement != "RESTORE":
            raise FleetError("Risk acknowledgement must equal RESTORE")
        plan = self.preview_restore(operation_id)
        if not plan or not plan["executable"]:
            raise FleetError("Restore preflight has critical risks")
        source = self._host(original["source_host"])
        target = self._host(original["target_host"])
        source_id = original["session_ids"][0]
        target_id = original["forked_session_ids"][0]
        restore_id, restore_dir = self.audit.new_operation("cross_host_restore")
        manifest = {
            "operation_id": restore_id,
            "kind": "cross_host_restore",
            "status": "preparing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "restores": operation_id,
            "source_host": original["source_host"],
            "target_host": original["target_host"],
            "session_ids": [source_id, target_id],
            "files": [],
            "databases": [],
            "risks": plan["risks"],
        }
        self.audit.write_manifest(restore_dir, manifest)
        source_unarchived = False
        try:
            target_session = {item.id: item for item in target.sessions()}[target_id]
            payload = target.fetch_rollout(target_session.rollout_path)
            entry = self.audit.backup_bytes(
                restore_dir,
                payload,
                f"target/{original['target_host']}-{target_id}.jsonl",
                f"{original['target_host']}:{target_session.rollout_path}",
            )
            entry.update({"host_id": original["target_host"], "session_id": target_id})
            manifest["files"].append(entry)
            manifest["databases"].extend(source.backup_databases(self.audit, restore_dir, 0))
            manifest["databases"].extend(
                target.backup_databases(self.audit, restore_dir, len(manifest["databases"]))
            )
            manifest["status"] = "backed_up"
            self.audit.write_manifest(restore_dir, manifest)
            if original["kind"] == "cross_host_move":
                source.set_archived(source_id, False)
                source_unarchived = True
            target.delete_thread(target_id)
            manifest["status"] = "completed"
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            self.audit.write_manifest(restore_dir, manifest)
            original["restored_by"] = restore_id
            self.audit.write_manifest(self.audit.operations / operation_id, original)
            self.audit.append_event(restore_id, "cross_host_restore", "completed", {"restores": operation_id})
            self._cache = None
            return manifest
        except Exception as exc:
            rollback_error = None
            if source_unarchived:
                try:
                    source.set_archived(source_id, True)
                except Exception as rollback_exc:
                    rollback_error = str(rollback_exc)
            manifest["status"] = "rollback_failed" if rollback_error else "rolled_back"
            manifest["error"] = str(exc)
            if rollback_error:
                manifest["rollback_error"] = rollback_error
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            self.audit.write_manifest(restore_dir, manifest)
            self.audit.append_event(restore_id, "cross_host_restore", manifest["status"], {"error": str(exc)})
            raise FleetError(f"Cross-host restore failed: {exc}") from exc

    @contextlib.contextmanager
    def _exclusive_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, BlockingIOError) as exc:
                    raise FleetError("Another Codex Transfer operation is already running") from exc
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
