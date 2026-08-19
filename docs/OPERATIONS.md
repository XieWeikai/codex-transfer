# Installation and Operations

## Install

Requirements are Python 3.11+, Codex CLI, and a supported local Codex state database.

```bash
./install.sh
codex-transfer
```

The installer uses an isolated environment and creates `codex-transfer` and `ct` commands in `~/.local/bin`. It refuses to replace an existing regular file. To run from source, create a virtual environment and install the repository with `pip install -e .`.

When upgrading from the former project name, only legacy symlinks owned by that installation are removed. Its data remains available; use `--data-dir ~/.codex-session-manager` to inspect or restore an older audit generation.

It also links the bundled `codex-transfer` Agent Skill into `~/.agents/skills` for Codex and `~/.claude/skills` for Claude Code. Existing non-symlink skill paths are skipped rather than overwritten.

## CLI operations

The CLI and Web UI call the same `MigrationEngine`; the safety, backup, audit, and restore rules are identical.

```bash
ct status --json
ct hosts --json
ct sessions --provider PROVIDER --project /exact/project --status ready --json
ct operations --limit 20 --json
```

Every write has a separate preview command and an exact acknowledgement:

```bash
ct fork-preview --session SESSION_ID --target TARGET --json
ct fork --session SESSION_ID --target TARGET --acknowledge FORK --json

ct move-preview --session SESSION_ID --source SOURCE --target TARGET --json
ct move --session SESSION_ID --source SOURCE --target TARGET --acknowledge MIGRATE --json

ct archive-preview --session SESSION_ID --json
ct archive --session SESSION_ID --acknowledge ARCHIVE --json

ct unarchive-preview --session SESSION_ID --json
ct unarchive --session SESSION_ID --acknowledge UNARCHIVE --json

ct archive-preview --host G1 --session SESSION_ID --json
ct archive --host G1 --session SESSION_ID --acknowledge ARCHIVE --json

ct restore-preview --operation OPERATION_ID --json
ct restore --operation OPERATION_ID --acknowledge RESTORE --json
```

Repeat `--session` to select multiple sessions. Batch Fork, Archive, and Unarchive execute one audited operation at a time and are not atomic; if an item fails, completed items remain and later items are not attempted.

### Cross-host Fork or Move

Codex Desktop must already have a passwordless SSH project connection open. Resolve the host ID with `ct hosts --json`, then use an absolute Project path that already exists on the target host:

```bash
ct fork-preview --session SESSION_ID --source-host SOURCE_HOST --target-host TARGET_HOST \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --json
ct fork --session SESSION_ID --source-host SOURCE_HOST --target-host TARGET_HOST \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --acknowledge FORK --json

ct move-preview --session SESSION_ID --source SOURCE_PROVIDER \
  --source-host SOURCE_HOST --target-host TARGET_HOST \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --json
```

Cross-host Move creates and verifies a new target thread before archiving the source. It does not delete the source. Batches are per-session and non-atomic; use each completed operation ID for independent restore.

The source and target host may be the same remote host. In that case `--target-cwd` is optional: Fork uses official `thread/fork`, while Move creates and verifies a new target-provider Session ID before archiving the source. Local-to-local Move retains its existing in-place metadata migration semantics.

## Active-session detection

Codex Transfer checks `thread-writer-locks/<session-id>.lock`. If the file is absent, the session is not locked. If it exists, Codex Transfer attempts a non-blocking exclusive `flock`: success means the file is stale or idle and the session is safe; failure means another Codex process owns the writer lock and every mutation is blocked. On platforms without `flock`, an existing lock file is treated conservatively as active.

Current Codex acquires the writer lock when `resume_thread` installs a live recorder. Closing a UI task unsubscribes the client but does not immediately unload that recorder. App-server waits until there have been no subscribers and no thread activity for 30 minutes, then emits `thread/closed`, drops the recorder, and releases the writer lock. Closing the entire owning app-server releases its locks sooner, but also affects every thread loaded by that process.

Do not delete or replace a lock file to force access. File locks are attached to an open file description/inode; deleting the pathname does not revoke the old process's kernel lock and may let a second writer lock a newly created file. Codex Transfer intentionally provides no force-takeover action.

## Live workspace updates

On macOS, Codex Transfer watches the local Codex home with `kqueue` and delivers small revision events to the browser with Server-Sent Events. Writer-lock directory changes trigger a lock-only refresh. SQLite, WAL, configuration, rename, and archive bursts are coalesced, then compared against a UI-visible metadata fingerprint before any workspace refresh. Ordinary message appends are filtered out. Hidden pages defer work until visible, so live updates do not turn into a full-workspace polling loop.

An existing Codex Desktop SSH proxy does not expose remote filesystem notifications to this process. Remote workspaces therefore refresh when first discovered, when selected, after a Codex Transfer operation, or when the user presses Refresh. The last successful snapshot remains visible during refresh, and scan completion is delivered through SSE rather than browser polling. Returning to the foreground updates local state without starting an SSH scan.

This detects write ownership, not human attention. Merely listing a session does not acquire the lock, but opening or resuming it does; once loaded, it can remain locked while idle until the unload delay expires. Recent `updated_at` timestamps and process-name matching are not used because they cannot establish exclusive write safety.

## Fork workflow

1. Stop the source task so the snapshot boundary is unambiguous.
2. Select Fork, one session, and a target provider.
3. Review encrypted-content, credential, model, tool, and provenance findings.
4. Confirm with `FORK`.
5. Codex Transfer backs up the source rollout and state database, then invokes `codex app-server` and its official `thread/fork` method.
6. Resume the new thread under the target provider and verify it before doing important work.

An unchanged fork can be removed through Operations. Once it receives new history, automatic undo is blocked.

## Move workflow

1. Quit or stop every selected Codex task.
2. Select Move, choose sessions from one source provider, and choose the target.
3. Confirm with `MIGRATE` after preflight.
4. Keep the backup until the moved sessions have been resumed and validated.

Move rewrites the original rollout and index. Restoration is available only while neither the rollout nor the database has changed since migration.

## Archive workflow

1. Stop the Codex task, select its local or SSH host, and use the Archive/Unarchive icon on its card.
2. Review the current-state and writer-lock preflight.
3. Confirm with `ARCHIVE` or `UNARCHIVE`.
4. Codex Transfer stores the selected host's rollout and a consistent SQLite snapshot in the local audit directory.
5. Codex Transfer invokes the selected host's official `thread/archive` or `thread/unarchive` app-server method and verifies the resulting indexed state.

Archive changes default-list visibility and may move the rollout under Codex's archive storage. It does not delete history or change Provider. Batch operations are intentionally per-session so each completed item has an independent recovery authority.

## Custom locations

```bash
codex-transfer --codex-home /path/to/.codex --data-dir /private/backup/path --port 8765
```

Use `--codex-bin` when Codex CLI is not on `PATH`. The server binds only to loopback and mutating requests require the per-process browser token.
