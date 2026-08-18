from __future__ import annotations

import contextlib
import os
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import AuditStore, sha256_database, sha256_file
from .model import MigrationPlan, Risk, require_safe_identifier
from .repository import CodexRepository, RepositoryError

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


class MigrationError(RuntimeError):
    pass


class MigrationEngine:
    """Deep module for previewing, executing and restoring provider migrations."""

    def __init__(self, repository: CodexRepository, audit: AuditStore):
        self.repository = repository
        self.audit = audit
        self.lock_path = audit.root / "manager.lock"

    def status(self) -> dict[str, Any]:
        databases = []
        for path in self.repository.state_db_paths():
            databases.append(
                {
                    "path": str(path),
                    "integrity": self.repository.integrity_check(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        sessions = self.repository.scan_sessions()
        return {
            "codex_home": str(self.repository.home),
            "data_dir": str(self.audit.root),
            "databases": databases,
            "session_count": len(sessions),
            "locked_session_count": sum(session.locked for session in sessions),
            "providers": self.repository.provider_ids(),
            "audit_chain_valid": self.audit.verify_chain(),
        }

    def preview(
        self, session_ids: list[str], source_provider: str, target_provider: str
    ) -> MigrationPlan:
        source = require_safe_identifier(source_provider, "source provider")
        target = require_safe_identifier(target_provider, "target provider")
        if source == target:
            raise MigrationError("Source and target providers must differ")
        if not session_ids:
            raise MigrationError("Select at least one session")
        sessions = self.repository.sessions_by_id(session_ids)
        risks: list[Risk] = []

        if target not in self.repository.provider_ids():
            risks.append(
                Risk(
                    "warning",
                    "target-not-configured",
                    f"目标 provider {target!r} 不在当前 Codex 配置中。",
                    "恢复该会话前，请先创建或启用这个 provider。",
                )
            )

        for session in sessions:
            if session.provider != source:
                risks.append(
                    Risk(
                        "critical",
                        "source-changed",
                        f"会话 {session.id} 当前属于 {session.provider!r}，不是 {source!r}。",
                        "刷新列表，并且只选择同一个来源 provider 下的会话。",
                    )
                )
            if session.locked:
                risks.append(
                    Risk(
                        "critical",
                        "session-active",
                        f"会话 {session.id} 正被 Codex 写入。",
                        "关闭或停止该 Codex 任务，然后重新运行预检。",
                    )
                )
            if not Path(session.rollout_path).exists():
                risks.append(
                    Risk(
                        "critical",
                        "rollout-missing",
                        f"会话 {session.id} 的 rollout 文件缺失。",
                        "先从可靠备份恢复文件，或清理失效的数据库记录。",
                    )
                )
            elif session.rollout_provider != session.provider:
                risks.append(
                    Risk(
                        "critical",
                        "metadata-mismatch",
                        f"会话 {session.id} 在 SQLite 中属于 {session.provider!r}，"
                        f"但 rollout 中记录为 {session.rollout_provider!r}。",
                        "先用可信备份修复这条不一致的会话，再执行迁移。",
                    )
                )

        db_paths = {Path(session.db_path) for session in sessions}
        for db_path in db_paths:
            integrity = self.repository.integrity_check(db_path)
            if integrity != "ok":
                risks.append(
                    Risk(
                        "critical",
                        "database-integrity",
                        f"SQLite 完整性检查失败：{db_path}，结果为 {integrity}",
                        "迁移前先修复或恢复 Codex 状态数据库。",
                    )
                )

        risks.extend(
            [
                Risk(
                    "warning",
                    "model-compatibility",
                    "目标 provider 可能不支持会话记录的模型、工具、推理等级或 API 模式。",
                    "恢复会话前，请核对目标 provider 的模型映射和能力。",
                ),
                Risk(
                    "warning",
                    "codex-version",
                    "Codex 本地存储不是稳定的公开迁移接口，后续版本可能改变结构。",
                    "在迁移后的会话成功恢复使用前，请保留完整备份。",
                ),
                Risk(
                    "info",
                    "credentials-not-moved",
                    "本工具不会复制或修改 provider 凭据及 config.toml 配置。",
                    "请在 Codex 或 CC Switch 中单独管理目标 provider 的凭据。",
                ),
            ]
        )
        db_size = sum(path.stat().st_size for path in db_paths)
        rollout_size = sum(session.size_bytes for session in sessions)
        return MigrationPlan(source, target, sessions, risks, db_size + rollout_size)

    def execute(
        self,
        session_ids: list[str],
        source_provider: str,
        target_provider: str,
        acknowledgement: str,
    ) -> dict[str, Any]:
        if acknowledgement != "MIGRATE":
            raise MigrationError("Risk acknowledgement must equal MIGRATE")
        with self._exclusive_lock():
            plan = self.preview(session_ids, source_provider, target_provider)
            if not plan.executable:
                raise MigrationError("Preflight has critical risks; migration was not started")
            operation_id, operation_dir = self.audit.new_operation("migration")
            manifest = self._manifest_base(operation_id, "migration", "preparing")
            manifest.update(
                {
                    "source_provider": plan.source_provider,
                    "target_provider": plan.target_provider,
                    "session_ids": [session.id for session in plan.sessions],
                    "files": [],
                    "databases": [],
                    "risks": [risk.to_dict() for risk in plan.risks],
                }
            )
            self.audit.write_manifest(operation_dir, manifest)
            try:
                for session in plan.sessions:
                    entry = self.audit.backup_file(
                        operation_dir, Path(session.rollout_path), f"rollouts/{session.id}.jsonl"
                    )
                    entry["session_id"] = session.id
                    manifest["files"].append(entry)
                db_groups = self._group_by_database(plan.sessions)
                for index, db_path in enumerate(db_groups):
                    entry = self.audit.backup_database(operation_dir, db_path, index)
                    entry["session_ids"] = db_groups[db_path]
                    manifest["databases"].append(entry)
                manifest["status"] = "backed_up"
                self.audit.write_manifest(operation_dir, manifest)
                self.audit.append_event(operation_id, "migration", "backed_up", {})

                for session in plan.sessions:
                    self.repository.rewrite_rollout_provider(
                        Path(session.rollout_path), session.id, plan.source_provider, plan.target_provider
                    )
                for db_path, ids in db_groups.items():
                    self.repository.update_db_provider(
                        db_path, ids, plan.source_provider, plan.target_provider
                    )

                for entry in manifest["files"]:
                    entry["after_sha256"] = sha256_file(Path(entry["source"]))
                for entry in manifest["databases"]:
                    entry["after_sha256"] = sha256_database(Path(entry["source"]))
                self._verify_result(plan)
                manifest["status"] = "completed"
                manifest["completed_at"] = self._now()
                self.audit.write_manifest(operation_dir, manifest)
                self.audit.append_event(
                    operation_id,
                    "migration",
                    "completed",
                    {"sessions": manifest["session_ids"]},
                )
                return manifest
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._restore_entries(operation_dir, manifest, check_hashes=False)
                manifest["status"] = "rolled_back"
                manifest["error"] = str(exc)
                manifest["completed_at"] = self._now()
                self.audit.write_manifest(operation_dir, manifest)
                self.audit.append_event(operation_id, "migration", "rolled_back", {"error": str(exc)})
                raise MigrationError(f"Migration failed and was rolled back: {exc}") from exc

    def restore(self, operation_id: str, acknowledgement: str) -> dict[str, Any]:
        if acknowledgement != "RESTORE":
            raise MigrationError("Risk acknowledgement must equal RESTORE")
        with self._exclusive_lock():
            original = self.audit.read_manifest(operation_id)
            if original.get("kind") != "migration" or original.get("status") != "completed":
                raise MigrationError("Only completed migration operations can be restored")
            if original.get("restored_by"):
                raise MigrationError("This migration has already been restored")
            current_sessions = self.repository.sessions_by_id(original["session_ids"])
            locked = [session.id for session in current_sessions if session.locked]
            if locked:
                raise MigrationError(
                    "恢复被阻止，以下会话正在被 Codex 写入：" + ", ".join(locked)
                )
            self._assert_post_hashes(original)

            restore_id, restore_dir = self.audit.new_operation("restore")
            manifest = self._manifest_base(restore_id, "restore", "preparing")
            manifest.update(
                {
                    "restores_operation": operation_id,
                    "session_ids": original["session_ids"],
                    "files": [],
                    "databases": [],
                }
            )
            try:
                for entry in original["files"]:
                    current = Path(entry["source"])
                    backup = self.audit.backup_file(
                        restore_dir, current, f"current/{entry['session_id']}.jsonl"
                    )
                    backup["session_id"] = entry["session_id"]
                    manifest["files"].append(backup)
                for index, entry in enumerate(original["databases"]):
                    current = Path(entry["source"])
                    backup = self.audit.backup_database(restore_dir, current, index)
                    backup["session_ids"] = entry["session_ids"]
                    manifest["databases"].append(backup)
                self.audit.write_manifest(restore_dir, manifest)
                self._restore_entries(self.audit.operations / operation_id, original, check_hashes=True)
                manifest["status"] = "completed"
                manifest["completed_at"] = self._now()
                self.audit.write_manifest(restore_dir, manifest)
                original["restored_by"] = restore_id
                original["restored_at"] = self._now()
                self.audit.write_manifest(self.audit.operations / operation_id, original)
                self.audit.append_event(restore_id, "restore", "completed", {"source": operation_id})
                return manifest
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._restore_entries(restore_dir, manifest, check_hashes=False)
                manifest["status"] = "rolled_back"
                manifest["error"] = str(exc)
                manifest["completed_at"] = self._now()
                self.audit.write_manifest(restore_dir, manifest)
                self.audit.append_event(restore_id, "restore", "rolled_back", {"error": str(exc)})
                raise MigrationError(f"Restore failed and current state was recovered: {exc}") from exc

    def _verify_result(self, plan: MigrationPlan) -> None:
        refreshed = {session.id: session for session in self.repository.scan_sessions()}
        failures = [
            session.id
            for session in plan.sessions
            if refreshed.get(session.id) is None
            or refreshed[session.id].provider != plan.target_provider
            or refreshed[session.id].rollout_provider != plan.target_provider
        ]
        if failures:
            raise RepositoryError(f"Post-migration verification failed: {', '.join(failures)}")

    def _assert_post_hashes(self, manifest: dict[str, Any]) -> None:
        for entry in manifest["files"]:
            path = Path(entry["source"])
            if not path.exists() or sha256_file(path) != entry.get("after_sha256"):
                raise MigrationError(
                    f"Current data changed after migration: {path}. Restore is blocked to prevent data loss."
                )
        for entry in manifest["databases"]:
            path = Path(entry["source"])
            if not path.exists() or sha256_database(path) != entry.get("after_sha256"):
                raise MigrationError(
                    f"Current data changed after migration: {path}. Restore is blocked to prevent data loss."
                )

    @staticmethod
    def _group_by_database(sessions) -> dict[Path, list[str]]:
        groups: dict[Path, list[str]] = defaultdict(list)
        for session in sessions:
            groups[Path(session.db_path)].append(session.id)
        return dict(groups)

    @staticmethod
    def _restore_entries(operation_dir: Path, manifest: dict[str, Any], check_hashes: bool) -> None:
        if check_hashes:
            for entry in manifest.get("files", []):
                if sha256_file(Path(entry["source"])) != entry["after_sha256"]:
                    raise MigrationError(f"Current file differs from audited post-state: {entry['source']}")
            for entry in manifest.get("databases", []):
                if sha256_database(Path(entry["source"])) != entry["after_sha256"]:
                    raise MigrationError(f"Current database differs from audited post-state: {entry['source']}")
        for entry in manifest.get("files", []):
            backup = operation_dir / entry["backup"]
            destination = Path(entry["source"])
            temp = destination.with_name(f".{destination.name}.csm-restore.tmp")
            try:
                shutil.copy2(backup, temp)
                with temp.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(temp, destination)
            finally:
                temp.unlink(missing_ok=True)
        for entry in manifest.get("databases", []):
            source_backup = operation_dir / entry["backup"]
            destination = Path(entry["source"])
            import sqlite3
            from contextlib import closing

            with closing(sqlite3.connect(source_backup)) as source_conn, closing(
                sqlite3.connect(destination)
            ) as dest_conn:
                source_conn.backup(dest_conn)

    @staticmethod
    def _manifest_base(operation_id: str, kind: str, status: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation_id": operation_id,
            "kind": kind,
            "status": status,
            "created_at": MigrationEngine._now(),
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @contextlib.contextmanager
    def _exclusive_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise MigrationError("Another manager operation is running") from exc
            yield
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
