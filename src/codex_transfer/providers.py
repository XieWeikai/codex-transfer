from __future__ import annotations

from collections import Counter
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .model import Session


def provider_catalog(
    config: dict[str, Any],
    sessions: Iterable[Session],
    *,
    host_id: str,
    config_source: str,
) -> list[dict[str, Any]]:
    """Build a display-safe catalog without exposing credential values."""
    session_list = list(sessions)
    counts = Counter(session.provider for session in session_list)
    active_counts = Counter(
        session.provider for session in session_list if not session.archived
    )
    locked_counts = Counter(session.provider for session in session_list if session.locked)
    archived_counts = Counter(session.provider for session in session_list if session.archived)
    configured = _mapping(config, "model_providers", "modelProviders")
    provider_ids = {"openai", *configured.keys(), *counts.keys()}

    catalog = []
    for provider_id in sorted(provider_ids, key=str.casefold):
        definition = configured.get(provider_id)
        details = definition if isinstance(definition, dict) else {}
        is_configured = provider_id in configured
        env_key = _text(details, "env_key", "envKey")
        requires_openai_auth = bool(
            _value(details, "requires_openai_auth", "requiresOpenaiAuth", default=False)
        )
        auth_type = _auth_type(
            details,
            env_key,
            requires_openai_auth or (provider_id == "openai" and not is_configured),
        )
        models = sorted(
            {
                session.model
                for session in session_list
                if session.provider == provider_id and session.model
            },
            key=str.casefold,
        )
        header_names = sorted(
            {
                *_mapping(details, "http_headers", "httpHeaders").keys(),
                *_mapping(details, "env_http_headers", "envHttpHeaders").keys(),
            },
            key=str.casefold,
        )
        query_names = sorted(
            _mapping(details, "query_params", "queryParams").keys(), key=str.casefold
        )
        catalog.append(
            {
                "id": provider_id,
                "host_id": host_id,
                "name": _text(details, "name")
                or ("OpenAI" if provider_id == "openai" else provider_id),
                "configured": is_configured,
                "source": config_source
                if is_configured
                else "Codex built-in / session metadata",
                "base_url": _safe_base_url(_text(details, "base_url", "baseUrl")),
                "wire_api": _text(details, "wire_api", "wireApi") or "responses",
                "auth_type": auth_type,
                "env_key": env_key if auth_type == "environment variable" else None,
                "supports_websockets": bool(
                    _value(details, "supports_websockets", "supportsWebsockets", default=False)
                ),
                "supports_standalone_web_search": bool(
                    _value(
                        details,
                        "supports_standalone_web_search",
                        "supportsStandaloneWebSearch",
                        default=False,
                    )
                ),
                "request_max_retries": _integer(
                    details, "request_max_retries", "requestMaxRetries"
                ),
                "stream_max_retries": _integer(
                    details, "stream_max_retries", "streamMaxRetries"
                ),
                "stream_idle_timeout_ms": _integer(
                    details, "stream_idle_timeout_ms", "streamIdleTimeoutMs"
                ),
                "header_names": header_names,
                "query_param_names": query_names,
                "session_count": counts[provider_id],
                "active_session_count": active_counts[provider_id],
                "archived_session_count": archived_counts[provider_id],
                "locked_session_count": locked_counts[provider_id],
                "models": models,
            }
        )
    return catalog


def _value(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _mapping(mapping: dict[str, Any], *keys: str) -> dict[str, Any]:
    value = _value(mapping, *keys, default={})
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _text(mapping: dict[str, Any], *keys: str) -> str | None:
    value = _value(mapping, *keys)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and not any(ord(char) < 32 for char in value) else None


def _integer(mapping: dict[str, Any], *keys: str) -> int | None:
    value = _value(mapping, *keys)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _auth_type(
    details: dict[str, Any], env_key: str | None, requires_openai_auth: bool
) -> str:
    if requires_openai_auth:
        return "OpenAI login / API key"
    if env_key:
        return "environment variable"
    if isinstance(details.get("auth"), dict):
        return "command-backed token"
    if isinstance(details.get("aws"), dict):
        return "AWS SigV4"
    if _value(details, "experimental_bearer_token", "experimentalBearerToken"):
        return "configured bearer token (value hidden)"
    return "not declared"


def _safe_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "configured endpoint (details hidden)"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "configured endpoint (details hidden)"
    try:
        port = parsed.port
    except ValueError:
        return "configured endpoint (details hidden)"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
