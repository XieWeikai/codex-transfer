# Design

## Goal

Fork or move selected local Codex sessions between `model_provider` buckets while making every write traceable and recoverable. Credentials and provider configuration are explicitly out of scope.

## Source findings

The implementation was checked against these source revisions on 2026-08-18:

- OpenAI Codex `230791fd1f255b9bd5ca5228326239db980f08dd`
- CC Switch `fd14f9c4fea57a0809bcc976622e77bc4191b8d5`

Codex reads provider metadata from the rollout `session_meta` record and persists a matching `model_provider` value in `state_5.sqlite.threads`. CC Switch's `codex_history_migration.rs` confirms that a provider bucket migration must update both representations and uses SQLite's backup interface before changing state. CC Switch also resolves `sqlite_home` and `CODEX_SQLITE_HOME`, which this project mirrors.

Codex app-server exposes `thread/fork` with a `modelProvider` override. Forking therefore crosses the supported Codex interface instead of recreating its lineage and persistence rules. The local adapter starts app-server over stdio with the selected `CODEX_HOME`, initializes one connection, submits the fork request, validates the returned durable thread, and shuts the process down cleanly.

## Deep modules

The design follows the `codebase-design` skill's depth, seam, and locality vocabulary.

### `MigrationEngine`

This is the external seam. Its interface covers migration preview/execute, Fork preview/execute, restore preview, and restore. Callers do not coordinate files, SQLite transactions, app-server lifecycle, backup generations, hashes, locks, or rollback themselves. Tests exercise the same interface as the HTTP adapter.

### `CodexRepository`

This module owns the unstable Codex storage seam. It discovers state databases, validates rollout paths, detects writer locks, parses session metadata, performs atomic rollout replacement, and updates SQLite transactionally. Storage-version changes remain local to this module.

### `CodexAppServer`

This adapter owns the official Fork seam. Its small interface accepts a source thread ID and target Provider and returns the durable fork identity. Production uses the stdio app-server adapter; tests use an in-process adapter that produces the same observable repository state.

### `AuditStore`

This module owns backup generations and the audit seam. Each operation directory contains a manifest, complete pre-write file copies, consistent SQLite snapshots, and SHA-256 values. The append-only audit JSONL uses a previous-event hash so accidental or unsophisticated edits are detectable.

## Operation state machine

```text
created -> preparing -> backed_up -> completed
                                \-> rolled_back
```

A restore is a new operation. Migration restore verifies the complete post-state before applying the original pre-state. Fork restore verifies that the created rollout is unchanged, snapshots the Fork, and removes only that thread and rollout. Both paths prevent restoration from silently deleting new messages.

## Trace portability analysis

Preflight scans JSONL one record at a time and counts structurally present `encrypted_content` values without returning message contents to the browser. Malformed JSONL records block writes. The scan deliberately does not claim per-turn Provider attribution because current rollout history does not contain a reliable provenance field for every model-produced item.

The resulting guarantee is intentionally narrow: backup bytes are recoverable while audited post-state hashes still match. Successful continuation, target-backend decryption, and reconstruction of mixed-provider provenance are not guaranteed.

## Consistency limits

There is no transaction spanning arbitrary JSONL files and multiple SQLite databases. The engine therefore uses a recoverable workflow: exclusive manager lock, active Codex writer-lock checks, complete backup, atomic file replacement, SQLite transactions, post-write verification, and automatic rollback on caught failures. Sudden power loss can still interrupt the workflow; the completed pre-write backup and manifest are the recovery authority.

## Security

- The server binds only to loopback.
- Mutating requests require an unpredictable per-process token embedded into the locally served page.
- Rollout paths must resolve beneath the selected Codex home.
- Operation IDs are restricted before filesystem resolution.
- Backup directories and manifests use user-only permissions where supported.
- The browser never receives provider credentials or rollout message contents.

## Interface model

The interface is a single operational workbench rather than a sequence of informational pages:

- The provider rail is the classification seam. It filters the session inventory and exposes counts without changing selection state.
- The session inventory is the selection seam. Search, status filters, sorting, click-to-add, and drag-to-add all produce the same queue state.
- The migration panel is the action seam. It keeps the target, queue, preflight, and execution controls together.
- The operations drawer is the recovery seam. It exposes backup generations, audit-chain status, operation results, and restore actions without replacing the current selection context.

Drag is progressive enhancement, not a requirement. Every draggable session has a click alternative; native form controls and focus order provide a keyboard path. Motion is reduced when the operating system requests it.

Risk guidance is contextual. Migration warnings are shown only after preflight and before execution; restore warnings are shown when restore is requested. Both flows require an acknowledgement plus an explicit confirmation phrase (`MIGRATE` or `RESTORE`). The execute control stays disabled until those conditions and the server-side preflight result are satisfied.

The visual system uses neutral graphite surfaces for dense operational data, green for the primary migration path, amber for cautions, red for destructive or blocked states, and blue/cyan only for secondary information. Layout decisions came from the actual provider-to-session-to-operation workflow; the installed `ui-ux-pro-max` skill supplied accessibility, interaction-target, typography, responsiveness, and reduced-motion checks rather than dictating an unrelated page template.
