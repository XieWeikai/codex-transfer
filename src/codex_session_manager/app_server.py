from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import __version__


class AppServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ForkResult:
    thread_id: str
    rollout_path: str
    model_provider: str


class ForkAdapter(Protocol):
    def fork(self, thread_id: str, target_provider: str) -> ForkResult: ...


class ArchiveAdapter(Protocol):
    def set_archived(self, thread_id: str, archived: bool) -> None: ...


class CodexAppServer:
    """Small stdio adapter around Codex's supported thread mutation interface."""

    def __init__(
        self,
        codex_home: Path,
        executable: str = "codex",
        timeout: float = 45.0,
        command: list[str] | None = None,
    ):
        self.codex_home = codex_home.expanduser().resolve()
        self.executable = executable
        self.timeout = timeout
        self.command = command

    def fork(self, thread_id: str, target_provider: str) -> ForkResult:
        result = self._request(
            "thread/fork",
            {
                "threadId": thread_id,
                "modelProvider": target_provider,
                "excludeTurns": True,
            },
        )
        thread = result.get("thread") or {}
        new_id = thread.get("id")
        rollout_path = thread.get("path")
        if not new_id or not rollout_path:
            raise AppServerError("Codex returned a fork without a durable thread id or path")
        return ForkResult(
            str(new_id),
            str(rollout_path),
            str(result.get("modelProvider") or target_provider),
        )

    def set_archived(self, thread_id: str, archived: bool) -> None:
        method = "thread/archive" if archived else "thread/unarchive"
        self._request(method, {"threadId": thread_id})

    def fork_from_path(
        self, rollout_path: str, target_provider: str, cwd: str | None = None
    ) -> ForkResult:
        params: dict[str, Any] = {
            "threadId": "path-import",
            "path": rollout_path,
            "modelProvider": target_provider,
            "excludeTurns": True,
        }
        if cwd:
            params["cwd"] = cwd
        return self._fork_result(self._request("thread/fork", params), target_provider)

    def delete(self, thread_id: str) -> None:
        self._request("thread/delete", {"threadId": thread_id})

    def list_threads(self) -> list[dict[str, Any]]:
        threads: list[dict[str, Any]] = []
        for archived in (False, True):
            cursor = None
            while True:
                params: dict[str, Any] = {
                    "limit": 500,
                    "sortKey": "updated_at",
                    "sortDirection": "desc",
                    "archived": archived,
                    "useStateDbOnly": True,
                }
                if cursor:
                    params["cursor"] = cursor
                result = self._request("thread/list", params)
                for thread in result.get("data", []):
                    if isinstance(thread, dict):
                        item = dict(thread)
                        item["archived"] = archived
                        threads.append(item)
                cursor = result.get("nextCursor")
                if not cursor:
                    break
        return threads

    def provider_ids(self) -> list[str]:
        result = self._request("config/read", {})
        config = result.get("config") or {}
        providers = config.get("model_providers") or config.get("modelProviders") or {}
        return sorted(
            {"openai", *(str(key) for key in providers if isinstance(providers, dict))},
            key=str.casefold,
        )

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._request(method, params)

    @staticmethod
    def _fork_result(result: dict[str, Any], target_provider: str) -> ForkResult:
        thread = result.get("thread") or {}
        new_id = thread.get("id")
        rollout_path = thread.get("path")
        if not new_id or not rollout_path:
            raise AppServerError("Codex returned a fork without a durable thread id or path")
        return ForkResult(
            str(new_id),
            str(rollout_path),
            str(result.get("modelProvider") or target_provider),
        )

    def _request(self, method: str, params: dict) -> dict:
        if self.command is None:
            executable = shutil.which(self.executable)
            if executable is None:
                raise AppServerError(
                    f"Cannot find {self.executable!r}. Install Codex CLI or pass --codex-bin."
                )
            command = [executable, "app-server", "--stdio"]
        else:
            command = self.command
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        try:
            self._send(
                process,
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "codex_session_manager",
                            "title": "Codex Relay",
                            "version": __version__,
                        },
                        "capabilities": {
                            "experimentalApi": True,
                            "optOutNotificationMethods": ["thread/started"],
                        },
                    },
                },
            )
            self._response(process, 1)
            self._send(process, {"method": "initialized", "params": {}})
            self._send(
                process,
                {
                    "method": method,
                    "id": 2,
                    "params": params,
                },
            )
            return self._response(process, 2)
        finally:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    @staticmethod
    def _send(process: subprocess.Popen[str], message: dict) -> None:
        if process.stdin is None:
            raise AppServerError("Codex app-server stdin is unavailable")
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _response(self, process: subprocess.Popen[str], request_id: int) -> dict:
        if process.stdout is None:
            raise AppServerError("Codex app-server stdout is unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while selector.select(self.timeout):
                line = process.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    error = message["error"]
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise AppServerError(f"Codex app-server rejected the request: {detail}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise AppServerError("Codex app-server returned an invalid response")
                return result
        finally:
            selector.close()
        stderr = process.stderr.read().strip() if process.poll() is not None and process.stderr else ""
        detail = f": {stderr[-800:]}" if stderr else ""
        raise AppServerError(f"Timed out waiting for Codex app-server{detail}")
