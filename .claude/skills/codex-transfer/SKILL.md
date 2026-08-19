---
name: codex-transfer
description: Inspect, filter, preflight, fork, move, archive, unarchive, restore, and audit Codex sessions across local and Desktop-connected SSH hosts with the Codex Transfer CLI.
---

# Codex Transfer CLI

Use the installed `codex-transfer` command, or its `ct` alias, to operate through the same migration engine as the Web UI.

## Establish the command

Check availability without modifying state:

```bash
command -v codex-transfer || command -v ct
```

If neither exists, report that installation is required. When working in this repository, the user can run `./install.sh`. Do not install or alter `CODEX_HOME` without authorization.

Use `--json` for every non-server command so IDs, risks, and failures remain machine-readable. Pass `--codex-home`, `--data-dir`, or `--codex-bin` only when the user supplies or confirms non-default locations.

## Read-only discovery

Run the narrowest useful command:

```bash
codex-transfer status --json
codex-transfer hosts --json
codex-transfer sessions --json
codex-transfer sessions --host HOST_ID --json
codex-transfer sessions --provider PROVIDER --project /exact/project/path --status ready --json
codex-transfer sessions --search QUERY --sort newest --json
codex-transfer operations --limit 20 --json
```

Valid session statuses are `all`, `ready`, `locked`, and `archived`. Valid sorts are `newest`, `oldest`, `title`, and `size`.

Never guess a Session ID, operation ID, source Provider, or target Provider. Resolve them from read-only output and ask the user when more than one target matches.

For cross-host work, use only host IDs returned by `hosts`. The target Project must be an existing absolute path on the target host. Codex Transfer never copies credentials or Provider configuration.

## Mutation safety gate

Fork, Move, Archive, Unarchive, and Restore write local Codex state. Perform one only when the user explicitly requested that exact operation and its targets.

Before every write:

1. Run the matching preview immediately before execution.
2. Inspect `executable` and every item in `risks`.
3. Stop when `executable` is false or any risk has severity `critical`.
4. Explain warnings that affect credentials, encrypted reasoning, provider provenance, batch atomicity, or restore data loss.
5. Confirm the selected IDs and target with the user if the current request did not already make them explicit.
6. Never bypass or synthesize a confirmation word without authorization for the write.

The target Provider must already be configured independently. This tool does not move credentials, API keys, OAuth state, Base URLs, or model aliases.

A session with `locked: true` currently has a held Codex writer lock and must not be mutated. This is not a recent-activity estimate: an idle UI tab can be open without a held lock. Never substitute timestamps or process-name matching for the lock result.

## Fork

Prefer Fork when the source Session matters. Repeat `--session` for a batch.

```bash
codex-transfer fork-preview \
  --session SESSION_ID \
  --target TARGET_PROVIDER \
  --json

codex-transfer fork \
  --session SESSION_ID \
  --target TARGET_PROVIDER \
  --acknowledge FORK \
  --json
```

Batch Fork is not atomic. Completed forks remain if a later item fails. Read `completed` and `failed`, report partial completion precisely, and do not retry successful items.

## Move

Move rewrites the original Session. All selected Sessions must belong to the same source Provider.

```bash
codex-transfer move-preview \
  --session SESSION_ID \
  --source SOURCE_PROVIDER \
  --target TARGET_PROVIDER \
  --json

codex-transfer move \
  --session SESSION_ID \
  --source SOURCE_PROVIDER \
  --target TARGET_PROVIDER \
  --acknowledge MIGRATE \
  --json
```

Stop active Codex tasks before previewing. Never edit rollout JSONL or SQLite directly as a substitute for this command.

## Cross-host Fork or Move

Add all three routing options to the normal Fork or Move command:

```bash
codex-transfer fork-preview --session SESSION_ID \
  --source-host SOURCE_HOST --target-host TARGET_HOST \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --json
```

Cross-host import uses experimental `thread/fork.path`. Prefer Fork. Cross-host Move creates and verifies a new target Session ID, then archives rather than deletes the source. A batch is not atomic; report every completed operation ID and the first failure exactly.

## Restore or undo

Resolve the operation from `operations`, then preview it:

```bash
codex-transfer restore-preview --operation OPERATION_ID --json
codex-transfer restore --operation OPERATION_ID --acknowledge RESTORE --json
```

Do not restore when the preview reports divergence. Restore can remove later conversation data; a Fork undo deletes the forked thread and rollout. When blocked, preserve the current state and report the changed paths without attempting a destructive workaround.

## Archive or unarchive

Archive changes default-list visibility without deleting history or changing Provider. Use the matching preview immediately before the write:

```bash
codex-transfer archive-preview --session SESSION_ID --json
codex-transfer archive --session SESSION_ID --acknowledge ARCHIVE --json

codex-transfer unarchive-preview --session SESSION_ID --json
codex-transfer unarchive --session SESSION_ID --acknowledge UNARCHIVE --json

codex-transfer archive-preview --host HOST_ID --session SESSION_ID --json
codex-transfer archive --host HOST_ID --session SESSION_ID --acknowledge ARCHIVE --json
```

Use a host ID returned by `hosts` for a Desktop-connected SSH session; omit `--host` for local. Repeat `--session` for multiple sessions. Archive batches are not atomic: report `completed` and `failed` precisely and do not retry completed items. Codex may move the rollout while changing archive state; never move it manually or update the SQLite `archived` field directly.

## Verify and report

After a successful write, run:

```bash
codex-transfer operations --limit 1 --json
codex-transfer sessions --search SESSION_ID --json
```

Report the operation ID, source and resulting Session IDs, target Provider, backup/audit status, warnings, and any remaining manual verification. Never print credentials or full Session contents.
