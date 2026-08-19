from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tomllib
from pathlib import Path
from typing import Iterable

from .model import Session, TraceProfile, ensure_within
from .providers import provider_catalog

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is conservative
    fcntl = None


class RepositoryError(RuntimeError):
    pass


class CodexRepository:
    """Owns the Codex storage seam: discovery, validation and coordinated writes."""

    STATE_DB = "state_5.sqlite"

    def __init__(self, codex_home: Path):
        self.home = codex_home.expanduser().resolve()

    def config(self) -> dict:
        path = self.home / "config.toml"
        if not path.exists():
            return {}
        try:
            with path.open("rb") as handle:
                return tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise RepositoryError(f"Cannot parse {path}: {exc}") from exc

    def provider_ids(self, sessions: Iterable[Session] | None = None) -> list[str]:
        configured = self.config().get("model_providers", {})
        ids = {"openai"}
        if isinstance(configured, dict):
            ids.update(str(key) for key in configured)
        for session in sessions if sessions is not None else self.scan_sessions():
            ids.add(session.provider)
        return sorted(ids, key=str.casefold)

    def provider_details(self, sessions: Iterable[Session] | None = None) -> list[dict]:
        items = list(sessions) if sessions is not None else self.scan_sessions()
        return provider_catalog(
            self.config(),
            items,
            host_id="local",
            config_source=str(self.home / "config.toml"),
        )

    def state_db_paths(self) -> list[Path]:
        candidates = [self.home / self.STATE_DB]
        config_home = self.config().get("sqlite_home")
        env_home = os.environ.get("CODEX_SQLITE_HOME")
        override = config_home if isinstance(config_home, str) and config_home.strip() else env_home
        if override:
            expanded = Path(override).expanduser().resolve() / self.STATE_DB
            if expanded not in candidates:
                candidates.append(expanded)
        return [path for path in candidates if path.exists()]

    def scan_sessions(self) -> list[Session]:
        sessions: dict[str, Session] = {}
        indexed_names = self._session_index_names()
        for db_path in self.state_db_paths():
            with self._connect(db_path, readonly=True) as conn:
                if not self._has_threads_schema(conn):
                    continue
                columns = self._columns(conn, "threads")
                model_expr = "model" if "model" in columns else "NULL"
                archived_expr = "archived" if "archived" in columns else "0"
                name_expr = "name" if "name" in columns else "NULL"
                first_message_expr = (
                    "first_user_message" if "first_user_message" in columns else "NULL"
                )
                history_mode_expr = "history_mode" if "history_mode" in columns else "'legacy'"
                rows = conn.execute(
                    f"""SELECT id, title, {name_expr} AS name,
                               {first_message_expr} AS first_user_message,
                               {history_mode_expr} AS history_mode, model_provider,
                               {model_expr} AS model, cwd,
                               updated_at, rollout_path, {archived_expr} AS archived
                        FROM threads ORDER BY updated_at DESC"""
                ).fetchall()
                for row in rows:
                    rollout_path = self._validated_rollout_path(row["rollout_path"])
                    rollout_provider = self.read_rollout_provider(rollout_path, row["id"])
                    size = rollout_path.stat().st_size if rollout_path.exists() else 0
                    sessions.setdefault(
                        row["id"],
                        Session(
                            id=row["id"],
                            title=self._resolve_display_title(row, indexed_names),
                            provider=row["model_provider"],
                            model=row["model"],
                            cwd=row["cwd"] or "",
                            updated_at=int(row["updated_at"]),
                            rollout_path=str(rollout_path),
                            db_path=str(db_path.resolve()),
                            archived=bool(row["archived"]),
                            locked=self.is_thread_locked(row["id"]),
                            rollout_provider=rollout_provider,
                            size_bytes=size,
                        ),
                    )
        return sorted(sessions.values(), key=lambda item: item.updated_at, reverse=True)

    def sessions_by_id(self, ids: Iterable[str]) -> list[Session]:
        wanted = list(dict.fromkeys(ids))
        indexed = {session.id: session for session in self.scan_sessions()}
        missing = [session_id for session_id in wanted if session_id not in indexed]
        if missing:
            raise RepositoryError(f"Unknown session IDs: {', '.join(missing)}")
        return [indexed[session_id] for session_id in wanted]

    def session_title(self, session_id: str) -> str:
        indexed_names = self._session_index_names()
        for db_path in self.state_db_paths():
            with self._connect(db_path, readonly=True) as conn:
                if not self._has_threads_schema(conn):
                    continue
                columns = self._columns(conn, "threads")
                name_expr = "name" if "name" in columns else "NULL"
                first_message_expr = (
                    "first_user_message" if "first_user_message" in columns else "NULL"
                )
                history_mode_expr = "history_mode" if "history_mode" in columns else "'legacy'"
                row = conn.execute(
                    f"""SELECT id, title, {name_expr} AS name,
                               {first_message_expr} AS first_user_message,
                               {history_mode_expr} AS history_mode
                        FROM threads WHERE id = ?""",
                    (session_id,),
                ).fetchone()
                if row is not None:
                    return self._resolve_display_title(row, indexed_names)
        raise RepositoryError(f"Unknown session ID: {session_id}")

    def _session_index_names(self) -> dict[str, str]:
        path = self.home / "session_index.jsonl"
        if not path.exists():
            return {}
        names: dict[str, str] = {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    thread_id = entry.get("id")
                    name = entry.get("thread_name")
                    if isinstance(thread_id, str) and isinstance(name, str) and name.strip():
                        names[thread_id] = name.strip()
        except OSError:
            return {}
        return names

    @staticmethod
    def _resolve_display_title(row: sqlite3.Row, indexed_names: dict[str, str]) -> str:
        title = str(row["title"] or "").strip()
        name = str(row["name"] or "").strip()
        history_mode = str(row["history_mode"] or "legacy")
        if history_mode == "paginated" and name:
            return name
        if history_mode == "legacy":
            first_message = str(row["first_user_message"] or "").strip()
            if first_message and title and title != first_message:
                return title
            indexed_name = indexed_names.get(str(row["id"]))
            if indexed_name:
                return indexed_name
        return name or title or "Untitled session"

    def integrity_check(self, db_path: Path) -> str:
        with self._connect(db_path, readonly=True) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return str(row[0]) if row else "no result"

    def read_rollout_provider(self, path: Path, session_id: str) -> str | None:
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if '"session_meta"' not in line or '"model_provider"' not in line:
                        continue
                    value = json.loads(line)
                    payload = value.get("payload", {})
                    if value.get("type") == "session_meta" and payload.get("id") == session_id:
                        provider = payload.get("model_provider")
                        return str(provider) if provider is not None else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return None

    def inspect_trace(self, path: Path, session_id: str) -> TraceProfile:
        """Inspect structural portability signals without retaining conversation content."""
        parsed = 0
        malformed = 0
        encrypted = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        malformed += 1
                        continue
                    parsed += 1
                    encrypted += self._count_encrypted_content(value)
        except OSError as exc:
            raise RepositoryError(f"Cannot inspect rollout {path}: {exc}") from exc
        return TraceProfile(session_id, parsed, malformed, encrypted)

    def rewrite_rollout_provider(
        self, path: Path, session_id: str, expected: str, target: str
    ) -> None:
        path = self._validated_rollout_path(str(path))
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        changed = 0
        output: list[str] = []
        for line in lines:
            replacement = line
            if '"session_meta"' in line and '"model_provider"' in line:
                value = json.loads(line)
                payload = value.get("payload", {})
                if value.get("type") == "session_meta" and payload.get("id") == session_id:
                    if payload.get("model_provider") != expected:
                        raise RepositoryError(
                            f"Rollout provider changed for {session_id}; expected {expected!r}"
                        )
                    payload["model_provider"] = target
                    newline = "\n" if line.endswith("\n") else ""
                    replacement = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + newline
                    changed += 1
            output.append(replacement)
        if changed != 1:
            raise RepositoryError(
                f"Expected one session_meta row for {session_id}, changed {changed}"
            )
        self._atomic_write(path, "".join(output).encode("utf-8"))

    def update_db_provider(
        self, db_path: Path, session_ids: list[str], expected: str, target: str
    ) -> None:
        placeholders = ",".join("?" for _ in session_ids)
        with self._connect(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"UPDATE threads SET model_provider = ? WHERE model_provider = ? "
                f"AND id IN ({placeholders})",
                [target, expected, *session_ids],
            )
            if cursor.rowcount != len(session_ids):
                conn.rollback()
                raise RepositoryError(
                    f"Database changed concurrently; expected {len(session_ids)} rows, "
                    f"updated {cursor.rowcount}"
                )
            conn.commit()

    def delete_fork(self, db_path: Path, session_id: str, rollout_path: Path) -> None:
        validated = self._validated_rollout_path(str(rollout_path))
        with self._connect(db_path) as conn:
            row = conn.execute(
                "SELECT rollout_path FROM threads WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None or Path(row[0]).resolve() != validated:
                raise RepositoryError("Fork thread no longer points to the audited rollout")
            changed = conn.execute("DELETE FROM threads WHERE id = ?", (session_id,)).rowcount
            if changed != 1:
                raise RepositoryError(f"Expected to delete one fork row, deleted {changed}")
            conn.commit()
        validated.unlink()

    def is_thread_locked(self, thread_id: str) -> bool:
        lock_path = self.home / "thread-writer-locks" / f"{thread_id}.lock"
        if not lock_path.exists():
            return False
        if fcntl is None:
            return True
        try:
            with lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
        except (OSError, BlockingIOError):
            return True

    def lock_snapshot(self) -> dict[str, bool]:
        thread_ids: set[str] = set()
        for db_path in self.state_db_paths():
            try:
                with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                    thread_ids.update(row[0] for row in conn.execute("SELECT id FROM threads"))
            except sqlite3.Error:
                continue
        return {thread_id: self.is_thread_locked(thread_id) for thread_id in thread_ids}

    def workspace_fingerprint(self) -> tuple:
        databases = []
        preferred = (
            "id",
            "name",
            "title",
            "model_provider",
            "cwd",
            "archived",
            "model",
            "rollout_path",
        )
        for db_path in self.state_db_paths():
            try:
                with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(threads)")}
                    selected = [column for column in preferred if column in columns]
                    rows = tuple(
                        sorted(conn.execute(f"SELECT {', '.join(selected)} FROM threads").fetchall())
                    )
                    databases.append((str(db_path), tuple(selected), rows))
            except sqlite3.Error as exc:
                databases.append((str(db_path), "error", str(exc)))
        def file_marker(path: Path) -> tuple[int, int] | None:
            try:
                stat = path.stat()
                return (stat.st_mtime_ns, stat.st_size)
            except OSError:
                return None

        # Older Codex builds can keep user-visible titles in session_index.jsonl
        # rather than the threads table, so it participates in the UI fingerprint.
        config_marker = file_marker(self.home / "config.toml")
        index_marker = file_marker(self.home / "session_index.jsonl")
        return (tuple(databases), config_marker, index_marker)

    def _validated_rollout_path(self, raw: str) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            path = self.home / path
        return ensure_within(path, self.home)

    @staticmethod
    @contextlib.contextmanager
    def _connect(path: Path, readonly: bool = False):
        target = f"file:{path}?mode=ro" if readonly else str(path)
        conn = sqlite3.connect(target, uri=readonly, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}

    @classmethod
    def _has_threads_schema(cls, conn: sqlite3.Connection) -> bool:
        return {"id", "title", "model_provider", "cwd", "updated_at", "rollout_path"}.issubset(
            cls._columns(conn, "threads")
        )

    @classmethod
    def _count_encrypted_content(cls, value: object) -> int:
        if isinstance(value, dict):
            own = int(bool(value.get("encrypted_content")))
            return own + sum(cls._count_encrypted_content(item) for item in value.values())
        if isinstance(value, list):
            return sum(cls._count_encrypted_content(item) for item in value)
        return 0

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temp = path.with_name(f".{path.name}.ct.tmp")
        try:
            with temp.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, path.stat().st_mode)
            os.replace(temp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()
