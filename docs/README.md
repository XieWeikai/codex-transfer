# Codex Relay Documentation

Codex Relay manages local Codex session placement without pretending that provider history is universally portable. It exposes two operations:

- **Fork** creates a new durable Codex thread through the official app-server `thread/fork` interface and asks Codex to use the target `modelProvider`. The source is unchanged.
- **Move** changes the original thread's provider bucket in both the rollout metadata and SQLite index.

Read these documents before operating on important sessions:

- [Architecture and storage model](DESIGN.md)
- [Risk model and recovery guarantees](SAFETY.md)
- [Installation and operating procedures](OPERATIONS.md)

The same concepts are available from the application's `/docs` page. The in-app copy is optimized for browsing; these Markdown files are the repository reference.
