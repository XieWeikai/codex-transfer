# Installation and Operations

## Install

Requirements are Python 3.11+, Codex CLI, and a supported local Codex state database.

```bash
./install.sh
codex-relay
```

The installer uses an isolated environment and creates `codex-relay` and `csm` commands in `~/.local/bin`. It refuses to replace an existing regular file. To run from source, create a virtual environment and install the repository with `pip install -e .`.

It also links the bundled `codex-relay` Agent Skill into `~/.agents/skills` for Codex and `~/.claude/skills` for Claude Code. Existing non-symlink skill paths are skipped rather than overwritten.

## CLI operations

The CLI and Web UI call the same `MigrationEngine`; the safety, backup, audit, and restore rules are identical.

```bash
csm status --json
csm hosts --json
csm sessions --provider PROVIDER --project /exact/project --status ready --json
csm operations --limit 20 --json
```

Every write has a separate preview command and an exact acknowledgement:

```bash
csm fork-preview --session SESSION_ID --target TARGET --json
csm fork --session SESSION_ID --target TARGET --acknowledge FORK --json

csm move-preview --session SESSION_ID --source SOURCE --target TARGET --json
csm move --session SESSION_ID --source SOURCE --target TARGET --acknowledge MIGRATE --json

csm archive-preview --session SESSION_ID --json
csm archive --session SESSION_ID --acknowledge ARCHIVE --json

csm unarchive-preview --session SESSION_ID --json
csm unarchive --session SESSION_ID --acknowledge UNARCHIVE --json

csm restore-preview --operation OPERATION_ID --json
csm restore --operation OPERATION_ID --acknowledge RESTORE --json
```

Repeat `--session` to select multiple sessions. Batch Fork, Archive, and Unarchive execute one audited operation at a time and are not atomic; if an item fails, completed items remain and later items are not attempted.

### Cross-host Fork or Move

Codex Desktop must already have a passwordless SSH project connection open. Resolve the host ID with `csm hosts --json`, then use an absolute Project path that already exists on the target host:

```bash
csm fork-preview --session SESSION_ID --source-host SOURCE_HOST --target-host TARGET_HOST \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --json
csm fork --session SESSION_ID --source-host SOURCE_HOST --target-host TARGET_HOST \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --acknowledge FORK --json

csm move-preview --session SESSION_ID --source SOURCE_PROVIDER \
  --source-host SOURCE_HOST --target-host TARGET_HOST \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --json
```

Cross-host Move creates and verifies a new target thread before archiving the source. It does not delete the source. Batches are per-session and non-atomic; use each completed operation ID for independent restore.

## Active-session detection

Codex Relay checks `thread-writer-locks/<session-id>.lock`. If the file is absent, the session is not locked. If it exists, Relay attempts a non-blocking exclusive `flock`: success means the file is stale or idle and the session is safe; failure means another Codex process owns the writer lock and every mutation is blocked. On platforms without `flock`, an existing lock file is treated conservatively as active.

This detects write ownership, not human attention. A session can be visible in an idle UI tab without holding the lock. Recent `updated_at` timestamps and process-name matching are not used because they cannot establish exclusive write safety.

## Fork workflow

1. Stop the source task so the snapshot boundary is unambiguous.
2. Select Fork, one session, and a target provider.
3. Review encrypted-content, credential, model, tool, and provenance findings.
4. Confirm with `FORK`.
5. Codex Relay backs up the source rollout and state database, then invokes `codex app-server` and its official `thread/fork` method.
6. Resume the new thread under the target provider and verify it before doing important work.

An unchanged fork can be removed through Operations. Once it receives new history, automatic undo is blocked.

## Move workflow

1. Quit or stop every selected Codex task.
2. Select Move, choose sessions from one source provider, and choose the target.
3. Confirm with `MIGRATE` after preflight.
4. Keep the backup until the moved sessions have been resumed and validated.

Move rewrites the original rollout and index. Restoration is available only while neither the rollout nor the database has changed since migration.

## Archive workflow

1. Stop every selected Codex task and choose Archive or Unarchive in the workbench.
2. Review the current-state and writer-lock preflight.
3. Confirm with `ARCHIVE` or `UNARCHIVE`.
4. Relay backs up the rollout and a consistent SQLite snapshot for each session.
5. Relay invokes the official `thread/archive` or `thread/unarchive` app-server method and verifies the resulting indexed state.

Archive changes default-list visibility and may move the rollout under Codex's archive storage. It does not delete history or change Provider. Batch operations are intentionally per-session so each completed item has an independent recovery authority.

## Custom locations

```bash
codex-relay --codex-home /path/to/.codex --data-dir /private/backup/path --port 8765
```

Use `--codex-bin` when Codex CLI is not on `PATH`. The server binds only to loopback and mutating requests require the per-process browser token.
