# Contributing to Codex Transfer

English | [简体中文](CONTRIBUTING_ZH.md)

Thank you for helping improve Codex Transfer. This project edits sensitive local state, so correctness, recoverability, and a narrow scope matter more than the size of a change.

By participating, you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md). Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not through a public issue.

## Ways to contribute

- Report a reproducible bug without attaching real session data.
- Propose a focused feature or storage-compatibility improvement.
- Improve tests, documentation, accessibility, or performance.
- Research a Codex storage or app-server change and document the exact source revision.
- Review pull requests and verify recovery behavior.

For behavior changes or new storage formats, open an issue before investing in a large implementation. Describe the user problem, affected Codex version, expected safety properties, and alternatives considered.

## Development setup

Requirements:

- Python 3.11 or newer
- Node.js only for JavaScript syntax checks
- Codex CLI for manual Fork integration testing

```bash
git clone https://github.com/XieWeikai/codex-transfer.git
cd codex-transfer
python3 -m venv .venv
.venv/bin/pip install -e .
```

Run the application against a disposable Codex home whenever possible:

```bash
.venv/bin/codex-transfer \
  --codex-home /path/to/disposable/.codex \
  --data-dir /path/to/disposable/backups \
  --port 8765
```

Never use private rollouts or credentials as test fixtures. Synthetic JSONL and SQLite fixtures belong in temporary directories created by the test suite.

## Architecture rules

Read [docs/DESIGN.md](docs/DESIGN.md) before changing write behavior. The main ownership boundaries are:

- `CodexRepository` owns Codex files, database discovery, schema checks, and atomic storage operations.
- `MigrationEngine` owns preflight, locking, backup, execution, verification, and rollback workflows.
- `CodexAppServer` owns the official `thread/fork` protocol boundary.
- `AuditStore` owns manifests, backup generations, hashes, and the audit chain.
- `server.py` and `static/` adapt those interfaces for the loopback web application.

Keep unstable Codex storage details inside the repository adapter. HTTP handlers and browser code must not coordinate file and SQLite mutations themselves.

Every new write path must define:

1. Its preconditions and writer-lock behavior.
2. The complete pre-state backup set.
3. Its atomicity boundary and rollback behavior.
4. Post-write verification.
5. The exact condition under which restore or undo must be blocked.
6. What the audit manifest records without exposing credentials.

## Tests and checks

Run these before submitting a pull request:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/codex_transfer/static/app.js
git diff --check
```

Add focused tests for changed behavior. Storage changes should cover success, interrupted or invalid input, rollback, divergence, and unknown-schema refusal where applicable.

For interface changes, manually verify:

- Keyboard and pointer alternatives for selection and drag operations.
- Light and dark themes.
- Narrow and wide layouts.
- Contextual risk confirmation before writes.
- Large workspaces without duplicating full session content in the DOM.

## Pull request workflow

1. Create a focused branch from `main`, such as `feat/project-filter` or `fix/restore-divergence`.
2. Keep one logical change per pull request.
3. Use Conventional Commit-style messages: `feat:`, `fix:`, `docs:`, `test:`, `perf:`, or `refactor:`.
4. Explain the user impact, safety implications, storage assumptions, and validation performed.
5. Link the issue when one exists.
6. Update both English and Chinese community documents when changing their shared meaning.

Do not mix formatting sweeps or unrelated refactors into a behavioral change. Maintainers may ask for a smaller pull request before reviewing implementation details.

## AI-assisted contributions

AI-assisted work is welcome, but the contributor remains responsible for every line. You must understand the change, verify it locally, remove invented claims, and avoid including private prompts or session data in generated artifacts. Large unreviewed generated patches may be closed because they transfer validation cost to maintainers.

## Licensing

By submitting a contribution, you agree that it may be distributed under the repository's [MIT License](LICENSE), and you confirm that you have the right to submit it.
