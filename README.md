<div align="center">

# Codex Relay

**Move or fork Codex sessions across providers and Desktop-connected hosts without giving up traceability.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-2f855a.svg)](LICENSE)
[![Loopback UI](https://img.shields.io/badge/web%20UI-loopback%20only-4c956c)](#security-model)
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
| Desktop SSH fleet | Discover the passwordless SSH hosts currently opened by Codex Desktop and browse each host independently. |
| Cross-host transfer | Fork from host A/provider A into host B/provider B and optionally archive the verified source as a reversible Move. |
| Safe Fork | Ask the official Codex app-server `thread/fork` interface to create a new durable thread under another provider. |
| Audited Move | Update the original rollout and SQLite index only after preflight and explicit confirmation. |
| Archive control | Archive or unarchive sessions through Codex app-server with per-session backup and audit records. |
| Contextual risk review | Show credential, encrypted-content, writer-lock, provenance, and recovery risks at the point of action. |
| Backup generations | Preserve rollout files, consistent SQLite snapshots, manifests, and SHA-256 values for every write. |
| Conflict-aware recovery | Refuse automatic restore or fork removal when later conversation data would be overwritten. |
| Large-workspace UI | Load bounded summaries, fetch long titles on demand, and update selections without rebuilding the grid. |

## How it works

```text
Codex home
  ├─ rollout JSONL ─┐
  └─ state SQLite ──┴─> preflight ─> snapshot ─> fork / move / archive ─> verify
                                      │                         │
                                      └──── audit manifest <────┘
```

- **Fork** leaves the source unchanged and delegates new-thread creation to Codex app-server.
- **Move** changes `session_meta.payload.model_provider` in the rollout and `threads.model_provider` in SQLite.
- **Cross-host Fork** stages an audited rollout copy on the target and asks its isolated Codex app-server to import it through experimental `thread/fork.path`.
- **Cross-host Move** creates and verifies a new target Session ID, then archives rather than deletes the source.
- **Archive / Unarchive** use Codex app-server `thread/archive` and `thread/unarchive`; they change visibility, not history or Provider.
- **Restore** is a new audited operation. It proceeds only when current hashes still match the recorded post-state.

Credentials, API keys, OAuth state, provider definitions, base URLs, and model aliases are never copied or stored by Codex Relay.

## Quick start

### Requirements

- Python 3.11 or newer
- Codex CLI available on `PATH` for Fork and archive operations
- A local Codex home using the supported `state_5.sqlite` thread schema
- For cross-host use: a passwordless Codex Desktop SSH connection, Python 3, and Codex CLI on the remote login `PATH`

### Install

```bash
git clone https://github.com/XieWeikai/codex-session-manager.git
cd codex-session-manager
./install.sh
codex-relay
```

The installer creates an isolated environment under `~/.local/share/codex-relay` and exposes `codex-relay` and `csm` under `~/.local/bin`. It also installs the bundled Agent Skill for Codex under `~/.agents/skills` and Claude Code under `~/.claude/skills`. Existing regular files or skill directories are never overwritten.

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

## CLI

Running `codex-relay` or `csm` without a subcommand still starts the Web UI. The CLI exposes the same engine operations for terminals and agents:

| Command | Purpose |
|---|---|
| `serve` | Start the local Web workbench explicitly. |
| `status` | Check Codex storage, database integrity, providers, locks, and audit-chain health. |
| `hosts` | List this Mac and SSH hosts currently connected by Codex Desktop. |
| `sessions` | Filter sessions by Provider, Project, status, search text, and sort order. |
| `operations` | List backup and audit operations. |
| `fork-preview` / `fork` | Preflight, then create provider forks with source preservation. |
| `move-preview` / `move` | Preflight, then move original sessions between Provider buckets. |
| `archive-preview` / `archive` | Preflight, then hide active sessions from the default Codex list. |
| `unarchive-preview` / `unarchive` | Preflight, then return archived sessions to the active list. |
| `restore-preview` / `restore` | Check divergence, then restore or undo an unchanged operation. |

Use `--json` for machine-readable output. Repeat `--session` for multi-selection:

```bash
csm hosts --json
csm sessions --host A100-1 --provider openai --project /path/to/project --status ready --json

csm fork-preview --session SESSION_ID --target TARGET_PROVIDER --json
csm fork --session SESSION_ID --target TARGET_PROVIDER --acknowledge FORK --json

csm fork-preview --session SESSION_ID --source-host A100-1 --target-host local \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --json
csm fork --session SESSION_ID --source-host A100-1 --target-host local \
  --target TARGET_PROVIDER --target-cwd /absolute/target/project --acknowledge FORK --json

csm move-preview --session SESSION_ID --source SOURCE --target TARGET --json
csm move --session SESSION_ID --source SOURCE --target TARGET \
  --acknowledge MIGRATE --json

csm archive-preview --session SESSION_ID --json
csm archive --session SESSION_ID --acknowledge ARCHIVE --json
csm unarchive-preview --session SESSION_ID --json
csm unarchive --session SESSION_ID --acknowledge UNARCHIVE --json

csm restore-preview --operation OPERATION_ID --json
csm restore --operation OPERATION_ID --acknowledge RESTORE --json
```

Preview commands never write session state. Write commands rerun preflight, create the same backups and audit records as the Web UI, and require the same explicit confirmation words.

A session is reported as `locked` when Codex Relay cannot acquire a non-blocking exclusive lock on `thread-writer-locks/<session-id>.lock`. This is a write-safety signal, not a recent-activity heuristic: an idle UI tab may be open without being locked, while a held writer lock always blocks mutations.

### Agent Skills

The repository includes a [Codex Skill](.agents/skills/codex-relay/SKILL.md) and a [Claude Code Skill](.claude/skills/codex-relay/SKILL.md). They instruct agents to discover IDs using read-only JSON commands, preview every write, stop on critical risks, and verify the resulting operation.

Repository-local skills load automatically while an agent works in this checkout. `./install.sh` also makes them available globally. Override the destinations with `CODEX_RELAY_CODEX_SKILLS_DIR` or `CODEX_RELAY_CLAUDE_SKILLS_DIR`.

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
- Cross-host import uses Codex's experimental `thread/fork.path`; incompatible CLI versions can reject it. Relay keeps source and target snapshots but cannot promise future protocol compatibility.
- Only SSH proxy processes directly owned by the local Codex/ChatGPT Desktop app are discovered. Arbitrary SSH config hosts and relay processes started solely on a remote machine are intentionally excluded.
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
