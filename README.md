# Codex Relay

Codex Relay is a local Web workbench for moving or forking Codex sessions between provider buckets with preflight checks, complete backups, a tamper-evident audit log, automatic rollback, and conflict-aware restore.

## Workbench

- Browse sessions by provider, status, keyword, or update time without exposing message contents.
- Drag sessions into the action queue, or use the equivalent **Add** button and keyboard controls.
- **Fork** uses Codex's official `thread/fork` interface and keeps the source session unchanged.
- **Move** updates the original session's provider bucket after an explicit high-risk confirmation.
- Preview first. Compatibility, active-writer, backup, and irreversible-risk guidance appears in the confirmation dialog at the moment it matters.
- Inspect every migration and restore from the **Operations & Recovery** drawer. Each operation links its manifest, hashes, backup generation, and result.

## What it changes

For each selected session, the manager updates both places used by current Codex session discovery:

- `session_meta.payload.model_provider` in the rollout JSONL
- `threads.model_provider` in Codex's `state_5.sqlite`

It does **not** move credentials, API keys, OAuth tokens, provider definitions, model aliases, or settings. Configure the target provider separately before resuming a migrated session.

## Requirements

- Python 3.11 or newer
- A local Codex installation using the currently supported `state_5.sqlite` schema
- Codex tasks being migrated must be stopped

## Install

The repository installer creates an isolated virtual environment under `~/.local/share/codex-relay` and exposes `codex-relay` plus the short `csm` alias under `~/.local/bin`:

```bash
./install.sh
codex-relay
```

Set `PYTHON_BIN`, `CODEX_RELAY_INSTALL_ROOT`, or `CODEX_RELAY_BIN_DIR` to override installer locations. Existing non-symlink commands are never overwritten.

## Run from source

From this directory:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/codex-relay
```

Then open `http://127.0.0.1:8765`.

Use another Codex home or backup directory when needed:

```bash
codex-relay \
  --codex-home /path/to/.codex \
  --data-dir /path/to/private/backups \
  --port 8765
```

The default backup directory is `~/.codex-session-manager`. Keep it private: rollout backups can contain prompts, code, file paths, and tool output.

## Safe workflow

1. Stop the Codex tasks you intend to move. For the safest operation, quit Codex entirely.
2. Choose **Fork** for the safer default, or **Move** when the original session must change provider.
3. Select one or more session cards with the checkbox, card click, or drag gesture, then choose the target provider.
4. Run preflight, resolve every critical finding, and enter `FORK` or `MIGRATE` as requested.
5. Start Codex with the target provider and verify the session before deleting any backup.
6. To revert, stop Codex again and use **操作与恢复**. Restore is blocked if the session changed after migration.

## Verification

Run the dependency-free test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Start with the [documentation index](docs/README.md). Architecture, source findings, invariants, trace portability, recovery semantics, and operating procedures are also available from the **Docs** link inside the application.

## Known limits

- Codex local storage is not a stable public interface. Unknown future schemas are refused rather than modified.
- A provider move changes session bucketing, not model compatibility.
- A provider fork preserves the source but cannot make encrypted reasoning portable.
- The audit hash chain is tamper-evident, not cryptographically signed. A privileged attacker can rewrite both backups and logs.
- Recovery after hard power loss may require using the operation manifest and backup files manually.
