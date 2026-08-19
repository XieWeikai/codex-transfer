# Design

## Goal

Fork or move selected Codex sessions between `model_provider` buckets and Codex Desktop-connected hosts, and manage local or remote archive visibility, while making every write traceable and recoverable. Credentials and provider configuration are explicitly out of scope.

## Cross-host boundary

`DesktopSshDiscovery` reads the local process tree and accepts only `ssh` processes whose direct parent is the Codex/ChatGPT Desktop app and whose command launches `codex app-server proxy`. That discovered alias is the complete remote-host authority; HTTP and CLI callers cannot inject arbitrary SSH destinations. Each operation starts a separate remote `codex app-server --stdio`, so inventory and mutations do not share or disrupt Desktop's daemon socket.

`HostFleet` owns inventory, preflight, transfer, archive state, rollback, and restore. Cross-host Fork stages a hash-verified rollout and uses Codex's experimental `thread/fork.path` request with a target Provider and cwd. Cross-host Move is verified target creation followed by source archive, never source deletion. Archive and Unarchive use the same selected-host adapter, so local and remote calls share the official app-server mutation, writer-lock checks, local audit snapshots, and rollback rules.

Codex's `ThreadForkParams` accepts either a thread ID or an experimental path, plus `model_provider` and `cwd`. Because the path option is explicitly unstable, version compatibility is a runtime risk rather than an invariant.

## Source findings

The implementation was checked against these source revisions on 2026-08-18:

- OpenAI Codex `230791fd1f255b9bd5ca5228326239db980f08dd`
- CC Switch `fd14f9c4fea57a0809bcc976622e77bc4191b8d5`

Codex reads provider metadata from the rollout `session_meta` record and persists a matching `model_provider` value in `state_5.sqlite.threads`. CC Switch's `codex_history_migration.rs` confirms that a provider bucket migration must update both representations and uses SQLite's backup interface before changing state. CC Switch also resolves `sqlite_home` and `CODEX_SQLITE_HOME`, which this project mirrors.

Codex app-server exposes `thread/fork` with a `modelProvider` override plus `thread/archive` and `thread/unarchive`. Forking and archive changes therefore cross the supported Codex interface instead of recreating its lineage, file-placement, and persistence rules. The local adapter starts app-server over stdio with the selected `CODEX_HOME`, initializes one connection, submits one request, validates the resulting durable state, and shuts the process down cleanly.

## Deep modules

The design follows the `codebase-design` skill's depth, seam, and locality vocabulary.

### `MigrationEngine`

This is the external seam. Its interface covers migration, Fork, archive and restore previews/execution. Callers do not coordinate files, SQLite transactions, app-server lifecycle, backup generations, hashes, locks, or rollback themselves. Tests exercise the same interface as the HTTP adapter.

The read side exposes one `workspace_snapshot` interface for the initial workbench load. It scans Codex storage once and returns status, bounded session summaries, and operation history together. Full session titles are fetched by ID only when a user deliberately opens a detail popover; internal rollout and database paths never enter the inventory response.

### `CodexRepository`

This module owns the unstable Codex storage seam. It discovers state databases, validates rollout paths, detects writer locks, parses session metadata, performs atomic rollout replacement, and updates SQLite transactionally. Storage-version changes remain local to this module.

### `CodexAppServer`

This adapter owns official Codex thread mutations. Its small interface accepts either a Fork request or a desired archive state. Production uses the stdio app-server adapter; tests use an in-process adapter that produces the same observable repository state.

### `AuditStore`

This module owns backup generations and the audit seam. Each operation directory contains a manifest, complete pre-write file copies, consistent SQLite snapshots, and SHA-256 values. The append-only audit JSONL uses a previous-event hash so accidental or unsophisticated edits are detectable.

### CLI and HTTP adapters

`cli.py` and `server.py` are adapters at the same `MigrationEngine` seam. They may select, format, and serialize data, but they do not implement storage mutations. Consequently, CLI and Web writes share preflight, locking, backup, confirmation, rollback, verification, audit, and divergence behavior. The CLI's `--json` form is the stable interface used by the bundled Codex and Claude Code Agent Skills.

## Operation state machine

```text
created -> preparing -> backed_up -> completed
                                \-> rolled_back
                                \-> rollback_failed
```

A restore is a new operation. Migration restore verifies the complete post-state before applying the original pre-state. Fork restore verifies that the created rollout is unchanged, snapshots the Fork, and removes only that thread and rollout. Both paths prevent restoration from silently deleting new messages.

## Trace portability analysis

Preflight scans JSONL one record at a time and counts structurally present `encrypted_content` values without returning message contents to the browser. Malformed JSONL records block writes. The scan deliberately does not claim per-turn Provider attribution because current rollout history does not contain a reliable provenance field for every model-produced item.

The resulting guarantee is intentionally narrow: backup bytes are recoverable while audited post-state hashes still match. Successful continuation, target-backend decryption, and reconstruction of mixed-provider provenance are not guaranteed.

## Consistency limits

There is no transaction spanning arbitrary JSONL files and multiple SQLite databases. The engine therefore uses a recoverable workflow: exclusive manager lock, active Codex writer-lock checks, complete backup, atomic file replacement, SQLite transactions, post-write verification, and automatic rollback on caught failures. Sudden power loss can still interrupt the workflow; the completed pre-write backup and manifest are the recovery authority.

Writer ownership is detected by attempting a non-blocking exclusive lock on Codex's per-thread lock file. File existence alone is insufficient because stale lock files can remain. Conversely, this signal establishes mutation safety rather than UI presence: an open idle tab may not hold a writer lock.

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

Large inventories are kept responsive by treating the grid as a stable view. Session summaries contain at most 240 title characters, cards render at most 120, and normalized search text is prepared once per load. Selecting or clearing sessions updates only the affected cards, counts, queue, and target state; the complete grid is rebuilt only when filtering or sorting changes what must be visible.

Risk guidance is contextual. Warnings are shown only after preflight and immediately before the requested mutation. Fork, migration, archive, unarchive, and restore require the matching explicit confirmation phrase (`FORK`, `MIGRATE`, `ARCHIVE`, `UNARCHIVE`, or `RESTORE`). The execute control stays disabled until the acknowledgement and server-side preflight result are satisfied.

The visual system uses neutral graphite surfaces for dense operational data, green for the primary migration path, amber for cautions, red for destructive or blocked states, and blue/cyan only for secondary information. Layout decisions came from the actual provider-to-session-to-operation workflow; the installed `ui-ux-pro-max` skill supplied accessibility, interaction-target, typography, responsiveness, and reduced-motion checks rather than dictating an unrelated page template.

### Event-driven workspace seam

`WorkspaceChangeMonitor` is the single interface between Codex-home change detection and HTTP clients. Its implementation owns native `kqueue` descriptors, burst coalescing, monotonically increasing revisions, and subscriber wakeups. The SSE handler only waits for revisions, while the browser decides whether a lock-only refresh or a workspace refresh is required. This keeps platform details and event-rate control local to one deep module.

The event stream carries only a revision and change kind, never Session contents. Local changes are automatic. Remote Desktop SSH adapters remain pull-based because the already-running proxy does not expose a multiplexed filesystem-event channel; they refresh on user and operation events rather than a background polling loop.

Remote inventory uses a stale-while-revalidate cache owned by `HostFleet`. Local filesystem events rebuild only local Session summaries and never evict a successful remote snapshot. A remote scan starts when Desktop exposes a new SSH host, when the user selects or refreshes a remote host, or when an operation invalidates one of its source or target hosts. Every host has an independent deadline and result, so one unavailable machine cannot keep the other host selectors loading. Successful Provider configuration reads are reused for five minutes; credentials remain inside the remote app-server process and are never cached or returned.

The browser does not poll `/api/hosts`. `HostFleet` publishes a workspace revision after each remote scan batch, and the existing SSE channel requests a new snapshot. During refresh the previous successful data remains selectable and is labeled as refreshing. A failed refresh retains that data with an explicit stale/error marker, performs one bounded retry for ordinary failures, and exits the loading state after a timeout.
