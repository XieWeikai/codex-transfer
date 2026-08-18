# Installation and Operations

## Install

Requirements are Python 3.11+, Codex CLI, and a supported local Codex state database.

```bash
./install.sh
codex-relay
```

The installer uses an isolated environment and creates `codex-relay` and `csm` commands in `~/.local/bin`. It refuses to replace an existing regular file. To run from source, create a virtual environment and install the repository with `pip install -e .`.

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

## Custom locations

```bash
codex-relay --codex-home /path/to/.codex --data-dir /private/backup/path --port 8765
```

Use `--codex-bin` when Codex CLI is not on `PATH`. The server binds only to loopback and mutating requests require the per-process browser token.
