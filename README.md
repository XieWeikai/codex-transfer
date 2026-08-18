# Codex Session Manager

A local Web workbench for moving selected Codex sessions between provider buckets with preflight checks, complete backups, a tamper-evident audit log, automatic rollback, and conflict-aware restore.

## Workbench

- Browse sessions by provider, status, keyword, or update time without exposing message contents.
- Drag sessions into the migration queue, or use the equivalent **Add** button and keyboard controls.
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

## Run

From this directory:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/codex-session-manager
```

Then open `http://127.0.0.1:8765`.

Use another Codex home or backup directory when needed:

```bash
codex-session-manager \
  --codex-home /path/to/.codex \
  --data-dir /path/to/private/backups \
  --port 8765
```

The default backup directory is `~/.codex-session-manager`. Keep it private: rollout backups can contain prompts, code, file paths, and tool output.

## Safe workflow

1. Stop the Codex tasks you intend to move. For the safest operation, quit Codex entirely.
2. Add sessions from one source provider to the queue by dragging or clicking **Add**, then choose the target provider.
3. Click **Preview migration**, resolve every critical finding, and read the contextual risk notice.
4. Confirm the acknowledgement and enter `MIGRATE`. Do not interrupt the process.
5. Start Codex with the target provider and verify the session before deleting any backup.
6. To revert, stop Codex again and use **操作与恢复**. Restore is blocked if the session changed after migration.

## Verification

Run the dependency-free test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Architecture, source findings, invariants, interface design, and limits are documented in [docs/DESIGN.md](docs/DESIGN.md).

## Known limits

- Codex local storage is not a stable public interface. Unknown future schemas are refused rather than modified.
- A provider move changes session bucketing, not model compatibility.
- The audit hash chain is tamper-evident, not cryptographically signed. A privileged attacker can rewrite both backups and logs.
- Recovery after hard power loss may require using the operation manifest and backup files manually.
