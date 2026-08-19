# Codex Relay Documentation

Codex Relay manages Codex session placement on this Mac and Codex Desktop-connected SSH hosts without pretending that provider history is universally portable. It exposes these operations:

- **Fork** creates a new durable Codex thread through the official app-server `thread/fork` interface and asks Codex to use the target `modelProvider`. The source is unchanged.
- **Move** changes the original thread's provider bucket in both the rollout metadata and SQLite index.
- **Cross-host Fork / Move** imports a new target thread through experimental `thread/fork.path`; Move archives the verified source instead of deleting it.
- **Archive / Unarchive** changes list visibility through the official app-server while preserving history and Provider.

Read these documents before operating on important sessions:

- [Architecture and storage model](DESIGN.md)
- [Risk model and recovery guarantees](SAFETY.md)
- [Installation and operating procedures](OPERATIONS.md)

The same concepts are available from the application's `/docs` page. The in-app copy is optimized for browsing; these Markdown files are the repository reference.
