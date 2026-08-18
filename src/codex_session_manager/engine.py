from __future__ import annotations

import contextlib
import os
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .app_server import ForkAdapter, ForkResult
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

    def __init__(
        self,
        repository: CodexRepository,
        audit: AuditStore,
        fork_adapter: ForkAdapter | None = None,
    ):
        self.repository = repository
        self.audit = audit
        self.fork_adapter = fork_adapter
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
        trace_profiles = []

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
            if Path(session.rollout_path).exists():
                trace_profiles.append(
                    self.repository.inspect_trace(Path(session.rollout_path), session.id)
                )

        encrypted_items = sum(profile.encrypted_content_items for profile in trace_profiles)
        encrypted_sessions = sum(profile.encrypted_content_items > 0 for profile in trace_profiles)
        malformed_records = sum(profile.malformed_records for profile in trace_profiles)
        if encrypted_items:
            risks.append(
                Risk(
                    "warning",
                    "encrypted-content-not-portable",
                    f"{encrypted_sessions} 个会话包含 {encrypted_items} 项 encrypted_content，"
                    "目标后端可能无法解密这些推理状态。",
                    "优先使用 Fork 保留原会话；迁移后先做一次恢复验证，并永久保留快照。",
                )
            )
        if malformed_records:
            risks.append(
                Risk(
                    "critical",
                    "trace-malformed",
                    f"检测到 {malformed_records} 条无法解析的 rollout 记录。",
                    "不要迁移可能损坏的 trace；先从可信备份修复或导出可见聊天内容。",
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
                    "provider-provenance-unavailable",
                    "Codex trace 没有可靠的逐轮 provider 归属；在目标 provider 继续聊天后会形成不可无损合并的混合历史。",
                    "迁移后若产生新消息，只能保留当前状态、克隆旧快照或破坏性回滚，无法自动逐轮还原来源。",
                ),
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
                    "warning",
                    "credentials-not-moved",
                    "本工具不会复制或修改 provider 凭据及 config.toml 配置。",
                    "请在 Codex 或 CC Switch 中单独管理目标 provider 的凭据。",
                ),
            ]
        )
        db_size = sum(path.stat().st_size for path in db_paths)
        rollout_size = sum(session.size_bytes for session in sessions)
        return MigrationPlan(source, target, sessions, risks, trace_profiles, db_size + rollout_size)

    def preview_fork(self, session_id: str, target_provider: str) -> MigrationPlan:
        session = self.repository.sessions_by_id([session_id])[0]
        plan = self.preview([session_id], session.provider, target_provider)
        plan.risks.append(
            Risk(
                "info",
                "source-preserved",
                "Fork 会创建新的 Codex thread，原会话及其 provider 归属保持不变。",
                "新 Fork 仍可能无法在目标后端解密历史 encrypted_content；先验证新副本再继续工作。",
            )
        )
        return plan

    def fork(self, session_id: str, target_provider: str, acknowledgement: str) -> dict[str, Any]:
        if acknowledgement != "FORK":
            raise MigrationError("Risk acknowledgement must equal FORK")
        if self.fork_adapter is None:
            raise MigrationError("Codex app-server fork adapter is not configured")
        with self._exclusive_lock():
            plan = self.preview_fork(session_id, target_provider)
            if not plan.executable:
                raise MigrationError("Preflight has critical risks; fork was not started")
            source = plan.sessions[0]
            operation_id, operation_dir = self.audit.new_operation("fork")
            manifest = self._manifest_base(operation_id, "fork", "preparing")
            manifest.update(
                {
                    "source_provider": source.provider,
                    "target_provider": plan.target_provider,
                    "session_ids": [source.id],
                    "forked_session_ids": [],
                    "files": [],
                    "created_files": [],
                    "databases": [],
                    "risks": [risk.to_dict() for risk in plan.risks],
                }
            )
            self.audit.write_manifest(operation_dir, manifest)
            result: ForkResult | None = None
            try:
                source_backup = self.audit.backup_file(
                    operation_dir, Path(source.rollout_path), f"source/{source.id}.jsonl"
                )
                source_backup["session_id"] = source.id
                manifest["files"].append(source_backup)
                db_path = Path(source.db_path)
                db_backup = self.audit.backup_database(operation_dir, db_path, 0)
                db_backup["session_ids"] = [source.id]
                manifest["databases"].append(db_backup)
                manifest["status"] = "backed_up"
                self.audit.write_manifest(operation_dir, manifest)
                self.audit.append_event(operation_id, "fork", "backed_up", {})

                result = self.fork_adapter.fork(source.id, plan.target_provider)
                forked = self.repository.sessions_by_id([result.thread_id])[0]
                if forked.provider != plan.target_provider or forked.rollout_provider != plan.target_provider:
                    raise MigrationError("Codex created the fork under an unexpected provider")
                if Path(forked.rollout_path).resolve() != Path(result.rollout_path).resolve():
                    raise MigrationError("Codex fork path does not match the indexed rollout path")
                manifest["forked_session_ids"] = [forked.id]
                manifest["created_files"] = [
                    {
                        "session_id": forked.id,
                        "source": forked.rollout_path,
                        "after_sha256": sha256_file(Path(forked.rollout_path)),
                        "size_bytes": forked.size_bytes,
                    }
                ]
                manifest["databases"][0]["after_sha256"] = sha256_database(db_path)
                manifest["status"] = "completed"
                manifest["completed_at"] = self._now()
                self.audit.write_manifest(operation_dir, manifest)
                self.audit.append_event(
                    operation_id,
                    "fork",
                    "completed",
                    {"source": source.id, "forked": forked.id},
                )
                return manifest
            except Exception as exc:
                if result is not None:
                    with contextlib.suppress(Exception):
                        self.repository.delete_fork(
                            Path(source.db_path), result.thread_id, Path(result.rollout_path)
                        )
                manifest["status"] = "rolled_back"
                manifest["error"] = str(exc)
                manifest["completed_at"] = self._now()
                self.audit.write_manifest(operation_dir, manifest)
                self.audit.append_event(operation_id, "fork", "rolled_back", {"error": str(exc)})
                raise MigrationError(f"Fork failed and was rolled back: {exc}") from exc

    def preview_restore(self, operation_id: str) -> dict[str, Any]:
        original = self.audit.read_manifest(operation_id)
        if original.get("kind") not in {"migration", "fork"} or original.get("status") != "completed":
            raise MigrationError("Only completed migration or fork operations can be restored")
        if original.get("restored_by"):
            raise MigrationError("This operation has already been restored")
        restore_ids = (
            original.get("forked_session_ids", [])
            if original.get("kind") == "fork"
            else original["session_ids"]
        )
        try:
            current_sessions = self.repository.sessions_by_id(restore_ids)
        except RepositoryError:
            current_sessions = []
        locked = [session.id for session in current_sessions if session.locked]
        changed = (
            self._created_file_changes(original)
            if original.get("kind") == "fork"
            else self._post_state_changes(original)
        )
        risks = []
        if len(current_sessions) != len(restore_ids):
            risks.append(
                Risk(
                    "critical",
                    "fork-missing",
                    "审计记录中的 Fork 已经不存在或未被 Codex 索引。",
                    "不要自动撤销；先检查操作清单和 Codex 状态数据库。",
                )
            )
        if locked:
            risks.append(
                Risk(
                    "critical",
                    "session-active",
                    "相关会话正在被 Codex 写入。",
                    "关闭这些任务后重新检查：" + ", ".join(locked),
                )
            )
        if changed:
            risks.append(
                Risk(
                    "critical",
                    "trace-diverged",
                    "操作后的会话已经变化，当前历史与审计快照发生分叉。",
                    "恢复会删除新增聊天，因此已阻止。请保留当前状态，或从旧快照创建独立副本。",
                )
            )
        if original.get("kind") == "fork":
            risks.append(
                Risk(
                    "warning",
                    "fork-removal",
                    "撤销会删除新 Fork 的 rollout 与索引；原 Session 不会改变。",
                    "只在确认新 Fork 未承载需要保留的工作时执行；目标 provider 凭据不会改变。",
                )
            )
        else:
            risks.append(
                Risk(
                    "warning",
                    "restore-provenance-limit",
                    "恢复只能还原迁移前字节，不能证明混合 trace 中每轮消息的 provider 来源。",
                    "恢复后仍需使用原 provider 验证会话；凭据和 provider 配置不会随快照恢复。",
                )
            )
        return {
            "operation_id": operation_id,
            "kind": original["kind"],
            "session_ids": restore_ids,
            "changed_paths": changed,
            "risks": [risk.to_dict() for risk in risks],
            "executable": not any(risk.severity == "critical" for risk in risks),
        }

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
            restore_plan = self.preview_restore(operation_id)
            if not restore_plan["executable"]:
                raise MigrationError("恢复预检发现阻断风险；当前状态未被修改")
            original = self.audit.read_manifest(operation_id)
            if original.get("kind") == "fork":
                return self._restore_fork(original)
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

    def _restore_fork(self, original: dict[str, Any]) -> dict[str, Any]:
        forked = self.repository.sessions_by_id(original["forked_session_ids"])[0]
        restore_id, restore_dir = self.audit.new_operation("restore")
        manifest = self._manifest_base(restore_id, "restore", "preparing")
        manifest.update(
            {
                "restores_operation": original["operation_id"],
                "restores_kind": "fork",
                "session_ids": [forked.id],
                "files": [],
                "databases": [],
            }
        )
        try:
            backup = self.audit.backup_file(
                restore_dir, Path(forked.rollout_path), f"fork/{forked.id}.jsonl"
            )
            backup["session_id"] = forked.id
            manifest["files"].append(backup)
            db_backup = self.audit.backup_database(restore_dir, Path(forked.db_path), 0)
            db_backup["session_ids"] = [forked.id]
            manifest["databases"].append(db_backup)
            self.audit.write_manifest(restore_dir, manifest)
            self.repository.delete_fork(
                Path(forked.db_path), forked.id, Path(forked.rollout_path)
            )
            manifest["status"] = "completed"
            manifest["completed_at"] = self._now()
            self.audit.write_manifest(restore_dir, manifest)
            original["restored_by"] = restore_id
            original["restored_at"] = self._now()
            self.audit.write_manifest(self.audit.operations / original["operation_id"], original)
            self.audit.append_event(
                restore_id, "restore", "completed", {"source": original["operation_id"]}
            )
            return manifest
        except Exception as exc:
            with contextlib.suppress(Exception):
                self._restore_entries(restore_dir, manifest, check_hashes=False)
            manifest["status"] = "rolled_back"
            manifest["error"] = str(exc)
            manifest["completed_at"] = self._now()
            self.audit.write_manifest(restore_dir, manifest)
            self.audit.append_event(restore_id, "restore", "rolled_back", {"error": str(exc)})
            raise MigrationError(f"Fork restore failed and current state was recovered: {exc}") from exc

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
        changed = self._post_state_changes(manifest)
        if changed:
            raise MigrationError(
                "Current data changed after migration; restore is blocked to prevent data loss: "
                + ", ".join(changed)
            )

    @staticmethod
    def _post_state_changes(manifest: dict[str, Any]) -> list[str]:
        changed = []
        for entry in manifest["files"]:
            path = Path(entry["source"])
            if not path.exists() or sha256_file(path) != entry.get("after_sha256"):
                changed.append(str(path))
        for entry in manifest["databases"]:
            path = Path(entry["source"])
            if not path.exists() or sha256_database(path) != entry.get("after_sha256"):
                changed.append(str(path))
        return changed

    @staticmethod
    def _created_file_changes(manifest: dict[str, Any]) -> list[str]:
        changed = []
        for entry in manifest.get("created_files", []):
            path = Path(entry["source"])
            if not path.exists() or sha256_file(path) != entry.get("after_sha256"):
                changed.append(str(path))
        return changed

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
