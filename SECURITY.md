# Security Policy

English | [简体中文](SECURITY_ZH.md)

## Supported versions

Codex Relay is pre-1.0 software that follows a latest-version support policy.

| Version | Supported |
|---|---|
| Latest release and `main` | Yes |
| Older revisions | No |

Storage compatibility is narrower than security support. A supported Codex Relay release may refuse to modify an unknown future Codex schema.

## Threat model

Codex Relay is a same-user local administration tool. It reads and, after explicit confirmation, writes Codex rollout files and SQLite indexes. It also creates local backups containing the same sensitive data.

The web server binds only to loopback. There is no project-operated cloud service, remote account system, or privilege separation from the operating-system user who launches the application. A process that already has full access to that user's files is generally inside the trust boundary.

Security properties the project intends to preserve include:

- Untrusted identifiers cannot escape the selected Codex home or backup root.
- Browser requests cannot mutate state without the current process token.
- Session selection cannot bypass preflight, backup, confirmation, or post-write verification.
- Restore and Fork undo cannot silently overwrite later conversation data.
- Credentials are never collected, copied into manifests, or returned to the browser.
- Malformed or unknown storage is rejected before a write.
- App-server invocation does not permit injection through session or provider values.

The audit chain is tamper-evident, not cryptographically signed. It does not protect against an attacker who can rewrite the application, audit log, and backups together.

## In scope

Examples of security issues that are in scope:

- Path traversal, symlink attacks, or identifier confusion that reads or writes outside authorized roots.
- Arbitrary file writes, unintended SQLite modification, SQL injection, or command injection.
- CSRF, cross-origin request abuse, token disclosure, XSS, or another path that performs a mutation without informed local confirmation.
- A restore or rollback that can overwrite diverged session history without detecting it.
- Backup, manifest, log, or API behavior that leaks credentials or session contents beyond the documented local files.
- Bypassing writer-lock, schema, integrity, or preflight checks in a way that corrupts Codex state.
- Audit-chain verification that accepts a demonstrably modified event sequence.
- Dependency, installer, or packaging behavior that executes attacker-controlled code without explicit user intent.

## Out of scope

The following are normally out of scope unless they form part of a larger trust-boundary bypass:

- An attacker who already controls the operating-system account and can freely edit both Codex state and Codex Relay backups.
- The user explicitly choosing a malicious `--codex-bin`, Codex home, or backup directory.
- Provider downtime, credential rejection, model incompatibility, or inability to decrypt backend-generated `encrypted_content`.
- Loss of per-turn provider provenance that the underlying Codex rollout never recorded.
- Denial of service against the user's own loopback instance without data corruption or boundary escape.
- Automated scanner output without a reproducible impact on a supported revision.
- Issues in unsupported Codex storage schemas that Codex Relay already refuses to modify.

## Reporting a vulnerability

Do **not** report vulnerabilities through public GitHub issues, discussions, or pull requests.

Use [GitHub Private Vulnerability Reporting](https://github.com/XieWeikai/codex-session-manager/security/advisories/new). If that form is unavailable, contact the repository owner through a private method listed on their GitHub profile and request a secure reporting channel.

Include:

- The affected Codex Relay revision and operating system.
- The affected Codex version or storage schema when relevant.
- The untrusted input source and complete path to the affected operation.
- Minimal reproduction steps using synthetic data.
- Expected and observed behavior.
- Potential confidentiality, integrity, or availability impact.
- Any suggested mitigation, if known.

Do not send real API keys, OAuth tokens, rollout files, database snapshots, or private prompts. Redact identifiers and construct a minimal synthetic reproduction.

## Response targets

These are best-effort targets, not a service-level agreement:

| Stage | Target |
|---|---|
| Acknowledgement | 3 business days |
| Initial assessment | 7 business days |
| Status updates | At least every 14 days while active |

Fix timing depends on severity, reproducibility, and upstream Codex behavior. We will coordinate disclosure with the reporter and credit them unless they request anonymity.

## Disclosure

Please allow time for a fix and release before public disclosure. Once a correction is available, the project may publish a GitHub Security Advisory describing affected versions, impact, mitigations, and reporter credit.

Good-faith research that respects privacy, avoids unnecessary data access, and follows this policy is welcome.
