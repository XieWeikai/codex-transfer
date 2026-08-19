from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from codex_transfer.app_server import CodexAppServer


class RecordingAppServer(CodexAppServer):
    def __init__(self) -> None:
        super().__init__(Path("/tmp/codex-transfer-test"))
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def _request(self, method: str, params: dict) -> dict:
        self.requests.append((method, dict(params)))
        if params.get("modelProviders") != []:
            return {"data": []}
        if params["archived"]:
            return {
                "data": [
                    {"id": "lowercase", "modelProvider": "openai"},
                    {"id": "configured", "modelProvider": "OpenAI"},
                ]
            }
        return {"data": [{"id": "active", "modelProvider": "OpenAI"}]}


class CodexAppServerListTest(unittest.TestCase):
    def test_lists_active_and_archived_threads_across_all_providers(self) -> None:
        server = RecordingAppServer()

        threads = server.list_threads()

        self.assertEqual(
            [
                (thread["id"], thread["modelProvider"], thread["archived"])
                for thread in threads
            ],
            [
                ("active", "OpenAI", False),
                ("lowercase", "openai", True),
                ("configured", "OpenAI", True),
            ],
        )
        self.assertEqual(len(server.requests), 2)
        for method, params in server.requests:
            self.assertEqual(method, "thread/list")
            self.assertEqual(params["modelProviders"], [])
            self.assertTrue(params["useStateDbOnly"])


if __name__ == "__main__":
    unittest.main()
