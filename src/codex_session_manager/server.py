from __future__ import annotations

import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import unquote, urlparse

from .engine import MigrationEngine, MigrationError
from .fleet import FleetError
from .repository import RepositoryError


class SessionManagerServer(ThreadingHTTPServer):
    def __init__(self, address, engine: MigrationEngine):
        self.engine = engine
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(address, SessionManagerHandler)


class SessionManagerHandler(BaseHTTPRequestHandler):
    server: SessionManagerServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/status":
                self._json(self.server.engine.status())
            elif path == "/api/workspace":
                self._json(self.server.engine.workspace_snapshot())
            elif path == "/api/hosts":
                self._json(self.server.engine.host_snapshot())
            elif path == "/api/sessions":
                self._json({"sessions": self.server.engine.workspace_snapshot()["sessions"]})
            elif path.startswith("/api/sessions/"):
                session_id = unquote(path.removeprefix("/api/sessions/"))
                title = self.server.engine.repository.session_title(session_id)
                self._json({"id": session_id, "title": title})
            elif path == "/api/operations":
                self._json({"operations": self.server.engine.audit.list_operations()})
            elif path in ("/", "/index.html"):
                content = self._static("index.html").replace(
                    b"__CSRF_TOKEN__", self.server.csrf_token.encode("ascii")
                )
                self._bytes(content, "text/html; charset=utf-8")
            elif path in ("/docs", "/docs/"):
                self._bytes(self._static("docs.html"), "text/html; charset=utf-8")
            elif path == "/app.js":
                self._bytes(self._static("app.js"), "text/javascript; charset=utf-8")
            elif path == "/docs.js":
                self._bytes(self._static("docs.js"), "text/javascript; charset=utf-8")
            elif path == "/styles.css":
                self._bytes(self._static("styles.css"), "text/css; charset=utf-8")
            elif path == "/docs.css":
                self._bytes(self._static("docs.css"), "text/css; charset=utf-8")
            else:
                self._error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self) -> None:
        try:
            if self.headers.get("X-CSM-Token") != self.server.csrf_token:
                self._error(HTTPStatus.FORBIDDEN, "Invalid request token")
                return
            payload = self._body()
            path = urlparse(self.path).path
            if path == "/api/preview":
                result = self.server.engine.preview(
                    payload.get("session_ids", []),
                    payload.get("source_provider", ""),
                    payload.get("target_provider", ""),
                ).to_dict()
            elif path == "/api/fork/preview":
                result = self.server.engine.preview_fork(
                    payload.get("session_id", ""), payload.get("target_provider", "")
                ).to_dict()
            elif path == "/api/forks/preview":
                result = self.server.engine.preview_forks(
                    payload.get("session_ids", []), payload.get("target_provider", "")
                ).to_dict()
            elif path == "/api/fork":
                result = self.server.engine.fork(
                    payload.get("session_id", ""),
                    payload.get("target_provider", ""),
                    payload.get("acknowledgement", ""),
                )
            elif path == "/api/migrate":
                result = self.server.engine.execute(
                    payload.get("session_ids", []),
                    payload.get("source_provider", ""),
                    payload.get("target_provider", ""),
                    payload.get("acknowledgement", ""),
                )
            elif path == "/api/transfer/preview":
                result = self.server.engine.preview_transfer(
                    payload.get("session_ids", []),
                    payload.get("source_host", ""),
                    payload.get("target_host", ""),
                    payload.get("target_provider", ""),
                    payload.get("target_cwd", ""),
                    bool(payload.get("move")),
                )
            elif path == "/api/transfer":
                result = self.server.engine.transfer(
                    payload.get("session_ids", []),
                    payload.get("source_host", ""),
                    payload.get("target_host", ""),
                    payload.get("target_provider", ""),
                    payload.get("target_cwd", ""),
                    bool(payload.get("move")),
                    payload.get("acknowledgement", ""),
                )
            elif path == "/api/archive/preview":
                result = self.server.engine.preview_archive(
                    payload.get("session_ids", []), payload.get("archived")
                )
            elif path == "/api/archive":
                result = self.server.engine.set_archived_batch(
                    payload.get("session_ids", []),
                    payload.get("archived"),
                    payload.get("acknowledgement", ""),
                )
            elif path.startswith("/api/operations/") and path.endswith("/restore-preview"):
                operation_id = path.removeprefix("/api/operations/").removesuffix(
                    "/restore-preview"
                )
                result = self.server.engine.preview_restore(operation_id)
            elif path.startswith("/api/operations/") and path.endswith("/restore"):
                operation_id = path.removeprefix("/api/operations/").removesuffix("/restore")
                result = self.server.engine.restore(
                    operation_id, payload.get("acknowledgement", "")
                )
            else:
                self._error(HTTPStatus.NOT_FOUND, "Not found")
                return
            self._json(result)
        except Exception as exc:
            self._handle_exception(exc)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} {format % args}")

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, (MigrationError, FleetError, RepositoryError, ValueError)):
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        else:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")
            print(f"[error] {type(exc).__name__}: {exc}")

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise ValueError("Request body is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _bytes(
        self, content: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content)

    @staticmethod
    def _static(name: str) -> bytes:
        return files("codex_session_manager.static").joinpath(name).read_bytes()
