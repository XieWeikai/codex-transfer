from __future__ import annotations

import argparse
import os
from pathlib import Path

from .audit import AuditStore
from .engine import MigrationEngine
from .repository import CodexRepository
from .server import SessionManagerServer


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Safely migrate Codex sessions between providers")
    result.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser(),
        help="Codex data directory (default: CODEX_HOME or ~/.codex)",
    )
    result.add_argument(
        "--data-dir",
        type=Path,
        default=Path("~/.codex-session-manager").expanduser(),
        help="Audit and backup directory",
    )
    result.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    result.add_argument("--port", type=int, default=8765)
    return result


def main() -> None:
    args = parser().parse_args()
    repository = CodexRepository(args.codex_home)
    audit = AuditStore(args.data_dir)
    engine = MigrationEngine(repository, audit)
    server = SessionManagerServer((args.host, args.port), engine)
    print(f"Codex Session Manager: http://{args.host}:{server.server_port}")
    print(f"Codex home: {repository.home}")
    print(f"Backups: {audit.root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

