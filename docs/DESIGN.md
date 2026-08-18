# Design

## Goal

Move selected local Codex sessions from one `model_provider` bucket to another while making every write traceable and reversible. Credentials and provider configuration are explicitly out of scope.

## Source findings

The implementation was checked against these source revisions on 2026-08-18:

- OpenAI Codex `230791fd1f255b9bd5ca5228326239db980f08dd`
- CC Switch `fd14f9c4fea57a0809bcc976622e77bc4191b8d5`

Codex reads provider metadata from the rollout `session_meta` record and persists a matching `model_provider` value in `state_5.sqlite.threads`. CC Switch's `codex_history_migration.rs` confirms that a provider bucket migration must update both representations and uses SQLite's backup interface before changing state. CC Switch also resolves `sqlite_home` and `CODEX_SQLITE_HOME`, which this project mirrors.

## Deep modules

The design follows the `codebase-design` skill's depth, seam, and locality vocabulary.

### `MigrationEngine`

This is the external seam. Its interface has three operations: `preview`, `execute`, and `restore`. Callers do not coordinate files, SQLite transactions, backup generations, hashes, locks, or rollback themselves. Tests exercise the same interface as the HTTP adapter.

### `CodexRepository`

This module owns the unstable Codex storage seam. It discovers state databases, validates rollout paths, detects writer locks, parses session metadata, performs atomic rollout replacement, and updates SQLite transactionally. Storage-version changes remain local to this module.

### `AuditStore`

This module owns backup generations and the audit seam. Each operation directory contains a manifest, complete pre-write file copies, consistent SQLite snapshots, and SHA-256 values. The append-only audit JSONL uses a previous-event hash so accidental or unsophisticated edits are detectable.

## Operation state machine

```text
created -> preparing -> backed_up -> completed
                                \-> rolled_back
```

A restore is a new operation. It first snapshots the current state, verifies that current hashes match the original migration's post-state, and then applies the original pre-state. This prevents restoration from silently deleting messages written after migration.

## Consistency limits

There is no transaction spanning arbitrary JSONL files and multiple SQLite databases. The engine therefore uses a recoverable workflow: exclusive manager lock, active Codex writer-lock checks, complete backup, atomic file replacement, SQLite transactions, post-write verification, and automatic rollback on caught failures. Sudden power loss can still interrupt the workflow; the completed pre-write backup and manifest are the recovery authority.

## Security

- The server binds only to loopback.
- Mutating requests require an unpredictable per-process token embedded into the locally served page.
- Rollout paths must resolve beneath the selected Codex home.
- Operation IDs are restricted before filesystem resolution.
- Backup directories and manifests use user-only permissions where supported.
- The browser never receives provider credentials or rollout message contents.

