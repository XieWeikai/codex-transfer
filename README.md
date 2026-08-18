<div align="center">

# Codex Relay

**Move or fork local Codex sessions between providers without giving up traceability.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-2f855a.svg)](LICENSE)
[![Local only](https://img.shields.io/badge/network-loopback%20only-4c956c)](#security-model)
[![Runtime](https://img.shields.io/badge/runtime-stdlib%20only-2f855a)](#development)

English | [简体中文](README_ZH.md) · [Documentation](docs/README.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

</div>

Codex Relay is a local web workbench for inspecting Codex sessions and safely changing their provider placement. Every write is preceded by preflight analysis and a complete backup, recorded in a tamper-evident audit trail, and guarded by post-state hashes before restore.

> [!CAUTION]
> A provider label is not a credential, and a session history is not universally portable. Encrypted reasoning, model-specific state, tools, and mixed-provider provenance may not survive a provider change. Prefer **Fork** when the source session matters.

## Why Codex Relay?

Moving a Codex session is not a file rename. Current Codex discovery uses both rollout JSONL metadata and a SQLite thread index. Editing only one can leave the session inconsistent or invisible. Codex Relay treats the operation as a recoverable workflow instead of a text replacement.

| Capability | What it does |
|---|---|
| Provider workspace | Browse compact session cards by provider, project, status, keyword, and update time. |
| Safe Fork | Ask the official Codex app-server `thread/fork` interface to create a new durable thread under another provider. |
| Audited Move | Update the original rollout and SQLite index only after preflight and explicit confirmation. |
| Contextual risk review | Show credential, encrypted-content, writer-lock, provenance, and recovery risks at the point of action. |
| Backup generations | Preserve rollout files, consistent SQLite snapshots, manifests, and SHA-256 values for every write. |
| Conflict-aware recovery | Refuse automatic restore or fork removal when later conversation data would be overwritten. |
| Large-workspace UI | Load bounded summaries, fetch long titles on demand, and update selections without rebuilding the grid. |

## How it works

```text
Codex home
  ├─ rollout JSONL ─┐
  └─ state SQLite ──┴─> preflight ─> snapshot ─> fork / move ─> verify
                                      │                         │
                                      └──── audit manifest <────┘
```

- **Fork** leaves the source unchanged and delegates new-thread creation to Codex app-server.
- **Move** changes `session_meta.payload.model_provider` in the rollout and `threads.model_provider` in SQLite.
- **Restore** is a new audited operation. It proceeds only when current hashes still match the recorded post-state.

Credentials, API keys, OAuth state, provider definitions, base URLs, and model aliases are never copied or stored by Codex Relay.

## Quick start

### Requirements

- Python 3.11 or newer
- Codex CLI available on `PATH` for Fork operations
- A local Codex home using the supported `state_5.sqlite` thread schema

### Install

```bash
git clone https://github.com/XieWeikai/codex-session-manager.git
cd codex-session-manager
./install.sh
codex-relay
```

The installer creates an isolated environment under `~/.local/share/codex-relay` and exposes `codex-relay` and `csm` under `~/.local/bin`. It refuses to overwrite an existing regular file.

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) after startup.

### Run from source

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/codex-relay
```

Custom locations and port:

```bash
codex-relay \
  --codex-home /path/to/.codex \
  --data-dir /path/to/private/backups \
  --codex-bin /path/to/codex \
  --port 8765
```

## Recommended workflow

1. Stop the Codex tasks you plan to operate on. Quit Codex entirely for the clearest snapshot boundary.
2. Select sessions from one source provider. Use project and status filters for large workspaces.
3. Choose **Fork** by default, or **Move** only when the original thread must change buckets.
4. Review every preflight finding and confirm with `FORK` or `MIGRATE`.
5. Resume the target thread and verify credentials, model mapping, tools, and conversation continuity.
6. Keep the backup until the migrated or forked session has been validated.

See [Operations](docs/OPERATIONS.md) for the complete procedure and [Safety and Recovery](docs/SAFETY.md) before operating on important sessions.

## Security model

Codex Relay is a same-user, local administration tool:

- The HTTP server accepts only `127.0.0.1` or `localhost`.
- Mutating requests require an unpredictable per-process token embedded in the locally served page.
- Rollout paths must resolve under the selected Codex home.
- Backup data stays local and receives user-only permissions where supported.
- Session message contents are not returned in the workspace inventory.

Backups are sensitive. They can contain prompts, source code, commands, paths, and tool output. The audit chain detects modification; it is not a digital signature and does not defend against an attacker who already controls your account.

Please report vulnerabilities privately according to [SECURITY.md](SECURITY.md). Do not include secrets or session content in public issues.

## Known limits

- Codex local storage is not a stable public API. Unknown schemas are refused rather than modified.
- Provider migration changes routing and discovery, not backend compatibility.
- Opaque `encrypted_content` may be unusable with another backend.
- Historical rollouts do not reliably identify the provider responsible for every turn; mixed-provider provenance cannot be reconstructed automatically.
- There is no transaction spanning JSONL files and multiple SQLite databases. Backups and rollback reduce risk but cannot make sudden power loss impossible.
- Restore is intentionally blocked after the post-operation state diverges, because overwriting new conversation data would be destructive.

## Documentation

| Guide | Purpose |
|---|---|
| [Documentation index](docs/README.md) | Start here for repository and in-app documentation. |
| [Design](docs/DESIGN.md) | Architecture, storage seams, invariants, and source findings. |
| [Safety](docs/SAFETY.md) | Credentials, encrypted reasoning, provenance, divergence, and backup privacy. |
| [Operations](docs/OPERATIONS.md) | Installation, Fork, Move, restore, and custom paths. |

The same conceptual documentation is available from the **Docs** button inside the workbench.

## Development

The runtime has no third-party Python dependencies. Run the complete test suite with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/codex_session_manager/static/app.js
```

Contributions are welcome when they preserve the project's backup, audit, and recovery invariants. Read [CONTRIBUTING.md](CONTRIBUTING.md) and our [Code of Conduct](CODE_OF_CONDUCT.md) first.

## License

Codex Relay is available under the [MIT License](LICENSE). An informational Chinese translation is available in [LICENSE_ZH.md](LICENSE_ZH.md); the English text is authoritative.

---

<div align="center">

Built for careful local operations. Not affiliated with or endorsed by OpenAI.

</div>
