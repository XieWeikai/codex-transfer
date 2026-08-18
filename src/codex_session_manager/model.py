from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class Session:
    id: str
    title: str
    provider: str
    model: str | None
    cwd: str
    updated_at: int
    rollout_path: str
    db_path: str
    archived: bool
    locked: bool
    rollout_provider: str | None
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TraceProfile:
    session_id: str
    parsed_records: int
    malformed_records: int
    encrypted_content_items: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Risk:
    severity: Severity
    code: str
    message: str
    remediation: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class MigrationPlan:
    source_provider: str
    target_provider: str
    sessions: list[Session]
    risks: list[Risk] = field(default_factory=list)
    trace_profiles: list[TraceProfile] = field(default_factory=list)
    estimated_backup_bytes: int = 0

    @property
    def executable(self) -> bool:
        return bool(self.sessions) and not any(r.severity == "critical" for r in self.risks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_provider": self.source_provider,
            "target_provider": self.target_provider,
            "sessions": [s.to_dict() for s in self.sessions],
            "risks": [r.to_dict() for r in self.risks],
            "trace_profiles": [profile.to_dict() for profile in self.trace_profiles],
            "estimated_backup_bytes": self.estimated_backup_bytes,
            "executable": self.executable,
        }


def require_safe_identifier(value: str, label: str) -> str:
    value = value.strip()
    if not value or len(value) > 128:
        raise ValueError(f"{label} must contain 1 to 128 characters")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} contains control characters")
    return value


def ensure_within(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    root_resolved = root.expanduser().resolve()
    if not resolved.is_relative_to(root_resolved):
        raise ValueError(f"Path escapes Codex home: {resolved}")
    return resolved
