---
name: codex-relay
description: Inspect, filter, preflight, fork, move, restore, and audit local Codex sessions with the Codex Relay CLI. Use when a user asks to find Codex sessions, change their provider, create a safer provider fork, inspect operation history, or recover an unchanged migration or fork.
---

# Codex Relay CLI

Use the installed `codex-relay` command, or its `csm` alias, to operate through the same migration engine as the Web UI.

## Establish the command

Check availability without modifying state:

```bash
command -v codex-relay || command -v csm
```

If neither exists, report that installation is required. When working in this repository, the user can run `./install.sh`. Do not install or alter `CODEX_HOME` without authorization.

Use `--json` for every non-server command so IDs, risks, and failures remain machine-readable. Pass `--codex-home`, `--data-dir`, or `--codex-bin` only when the user supplies or confirms non-default locations.

## Read-only discovery

Run the narrowest useful command:

```bash
codex-relay status --json
codex-relay sessions --json
codex-relay sessions --provider PROVIDER --project /exact/project/path --status ready --json
codex-relay sessions --search QUERY --sort newest --json
codex-relay operations --limit 20 --json
```

Valid session statuses are `all`, `ready`, `locked`, and `archived`. Valid sorts are `newest`, `oldest`, `title`, and `size`.

Never guess a Session ID, operation ID, source Provider, or target Provider. Resolve them from read-only output and ask the user when more than one target matches.

## Mutation safety gate

Fork, Move, and Restore write local Codex state. Perform one only when the user explicitly requested that exact operation and its targets.

Before every write:

1. Run the matching preview immediately before execution.
2. Inspect `executable` and every item in `risks`.
3. Stop when `executable` is false or any risk has severity `critical`.
4. Explain warnings that affect credentials, encrypted reasoning, provider provenance, batch atomicity, or restore data loss.
5. Confirm the selected IDs and target with the user if the current request did not already make them explicit.
6. Never bypass or synthesize a confirmation word without authorization for the write.

The target Provider must already be configured independently. This tool does not move credentials, API keys, OAuth state, Base URLs, or model aliases.

## Fork

Prefer Fork when the source Session matters. Repeat `--session` for a batch.

```bash
codex-relay fork-preview \
  --session SESSION_ID \
  --target TARGET_PROVIDER \
  --json

codex-relay fork \
  --session SESSION_ID \
  --target TARGET_PROVIDER \
  --acknowledge FORK \
  --json
```

Batch Fork is not atomic. Completed forks remain if a later item fails. Read `completed` and `failed`, report partial completion precisely, and do not retry successful items.

## Move

Move rewrites the original Session. All selected Sessions must belong to the same source Provider.

```bash
codex-relay move-preview \
  --session SESSION_ID \
  --source SOURCE_PROVIDER \
  --target TARGET_PROVIDER \
  --json

codex-relay move \
  --session SESSION_ID \
  --source SOURCE_PROVIDER \
  --target TARGET_PROVIDER \
  --acknowledge MIGRATE \
  --json
```

Stop active Codex tasks before previewing. Never edit rollout JSONL or SQLite directly as a substitute for this command.

## Restore or undo

Resolve the operation from `operations`, then preview it:

```bash
codex-relay restore-preview --operation OPERATION_ID --json
codex-relay restore --operation OPERATION_ID --acknowledge RESTORE --json
```

Do not restore when the preview reports divergence. Restore can remove later conversation data; a Fork undo deletes the forked thread and rollout. When blocked, preserve the current state and report the changed paths without attempting a destructive workaround.

## Verify and report

After a successful write, run:

```bash
codex-relay operations --limit 1 --json
codex-relay sessions --search SESSION_ID --json
```

Report the operation ID, source and resulting Session IDs, target Provider, backup/audit status, warnings, and any remaining manual verification. Never print credentials or full Session contents.
