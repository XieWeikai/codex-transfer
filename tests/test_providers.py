from __future__ import annotations

import json
import unittest

from codex_transfer.model import Session
from codex_transfer.providers import provider_catalog


def session(provider: str, *, archived: bool = False, locked: bool = False) -> Session:
    return Session(
        id=f"session-{provider}-{archived}-{locked}",
        title="Provider test",
        provider=provider,
        model="test-model",
        cwd="/work",
        updated_at=1,
        rollout_path="/codex/session.jsonl",
        db_path="/codex/state.sqlite",
        archived=archived,
        locked=locked,
        rollout_provider=provider,
        size_bytes=1,
    )


class ProviderCatalogTest(unittest.TestCase):
    def test_catalog_reports_routes_without_credential_values(self) -> None:
        config = {
            "model_providers": {
                "private": {
                    "name": "Private Gateway",
                    "base_url": "https://user:password@example.test/v1?secret=query",
                    "env_key": "PRIVATE_API_KEY",
                    "experimental_bearer_token": "never-return-this-token",
                    "http_headers": {"X-Tenant": "secret-tenant"},
                    "env_http_headers": {"X-Trace": "TRACE_SECRET"},
                    "query_params": {"api-version": "secret-version"},
                    "wire_api": "responses",
                    "supports_websockets": True,
                    "request_max_retries": 4,
                }
            }
        }
        catalog = provider_catalog(
            config,
            [session("private"), session("private", archived=True, locked=True)],
            host_id="remote-1",
            config_source="/home/test/.codex/config.toml",
        )
        private = next(item for item in catalog if item["id"] == "private")
        self.assertEqual(private["base_url"], "https://example.test/v1")
        self.assertEqual(private["auth_type"], "environment variable")
        self.assertEqual(private["env_key"], "PRIVATE_API_KEY")
        self.assertEqual(private["header_names"], ["X-Tenant", "X-Trace"])
        self.assertEqual(private["query_param_names"], ["api-version"])
        self.assertEqual(private["session_count"], 2)
        self.assertEqual(private["active_session_count"], 1)
        self.assertEqual(private["archived_session_count"], 1)
        self.assertEqual(private["locked_session_count"], 1)
        self.assertEqual(private["models"], ["test-model"])
        serialized = json.dumps(catalog)
        for secret in (
            "password",
            "secret=query",
            "never-return-this-token",
            "secret-tenant",
            "TRACE_SECRET",
            "secret-version",
        ):
            self.assertNotIn(secret, serialized)

    def test_builtin_and_session_only_providers_are_retained(self) -> None:
        catalog = provider_catalog(
            {}, [session("Historical")], host_id="local", config_source="config.toml"
        )
        self.assertEqual([item["id"] for item in catalog], ["Historical", "openai"])
        self.assertFalse(catalog[0]["configured"])
        self.assertEqual(catalog[1]["auth_type"], "OpenAI login / API key")


if __name__ == "__main__":
    unittest.main()
