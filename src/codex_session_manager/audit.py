from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_database(path: Path) -> str:
    """Hash a consistent logical SQLite snapshot, including committed WAL pages."""
    handle, temp_name = tempfile.mkstemp(suffix=".sqlite")
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with closing(sqlite3.connect(path)) as source_conn, closing(
            sqlite3.connect(temp_path)
        ) as snapshot_conn:
            source_conn.backup(snapshot_conn)
        return sha256_file(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


class AuditStore:
    """Owns immutable operation generations and the tamper-evident event chain."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.operations = self.root / "operations"
        self.operations.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.log_path = self.root / "audit.jsonl"

    def new_operation(self, kind: str) -> tuple[str, Path]:
        now = datetime.now(timezone.utc)
        operation_id = f"{now.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:12]}"
        operation_dir = self.operations / operation_id
        operation_dir.mkdir(mode=0o700)
        (operation_dir / "files").mkdir(mode=0o700)
        (operation_dir / "databases").mkdir(mode=0o700)
        self.append_event(operation_id, kind, "created", {})
        return operation_id, operation_dir

    def backup_file(self, operation_dir: Path, source: Path, logical_name: str) -> dict[str, Any]:
        destination = operation_dir / "files" / logical_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return {
            "source": str(source.resolve()),
            "backup": str(destination.relative_to(operation_dir)),
            "before_sha256": sha256_file(destination),
            "size_bytes": destination.stat().st_size,
        }

    def backup_bytes(
        self, operation_dir: Path, payload: bytes, logical_name: str, source: str
    ) -> dict[str, Any]:
        destination = operation_dir / "files" / logical_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(destination, 0o600)
        return {
            "source": source,
            "backup": str(destination.relative_to(operation_dir)),
            "before_sha256": sha256_file(destination),
            "size_bytes": len(payload),
        }

    def store_database_snapshot(
        self,
        operation_dir: Path,
        payload: bytes,
        index: int,
        source: str,
        host_id: str,
    ) -> dict[str, Any]:
        safe_host = "".join(char if char.isalnum() or char in "._-" else "_" for char in host_id)
        destination = operation_dir / "databases" / f"{safe_host[:128]}-state-{index}.sqlite"
        with destination.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(destination, 0o600)
        return {
            "host_id": host_id,
            "source": source,
            "backup": str(destination.relative_to(operation_dir)),
            "before_sha256": sha256_database(destination),
            "size_bytes": len(payload),
        }

    def backup_database(self, operation_dir: Path, source: Path, index: int) -> dict[str, Any]:
        destination = operation_dir / "databases" / f"state-{index}.sqlite"
        with closing(sqlite3.connect(source)) as source_conn, closing(
            sqlite3.connect(destination)
        ) as backup_conn:
            source_conn.backup(backup_conn)
        os.chmod(destination, 0o600)
        return {
            "source": str(source.resolve()),
            "backup": str(destination.relative_to(operation_dir)),
            "before_sha256": sha256_database(destination),
            "size_bytes": destination.stat().st_size,
        }

    def write_manifest(self, operation_dir: Path, manifest: dict[str, Any]) -> None:
        path = operation_dir / "manifest.json"
        temp = operation_dir / ".manifest.tmp"
        payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)

    def read_manifest(self, operation_id: str) -> dict[str, Any]:
        if not operation_id or any(char not in "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for char in operation_id):
            raise ValueError("Invalid operation ID")
        path = self.operations / operation_id / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def list_operations(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.operations.glob("*/manifest.json"), reverse=True):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                result.append(manifest)
            except (OSError, json.JSONDecodeError):
                continue
        return result

    def verify_chain(self) -> bool:
        previous = "0" * 64
        if not self.log_path.exists():
            return True
        try:
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                recorded_hash = event.pop("event_hash")
                if event.get("previous_hash") != previous:
                    return False
                canonical = json.dumps(
                    event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != recorded_hash:
                    return False
                previous = recorded_hash
            return True
        except (OSError, KeyError, json.JSONDecodeError):
            return False

    def append_event(
        self, operation_id: str, kind: str, status: str, details: dict[str, Any]
    ) -> None:
        previous = "0" * 64
        if self.log_path.exists():
            try:
                last = self.log_path.read_text(encoding="utf-8").splitlines()[-1]
                previous = json.loads(last)["event_hash"]
            except (OSError, IndexError, KeyError, json.JSONDecodeError):
                previous = "invalid-chain"
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation_id": operation_id,
            "kind": kind,
            "status": status,
            "details": details,
            "previous_hash": previous,
        }
        canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        event["event_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(self.log_path, 0o600)
