from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

from .app_server import AppServerError, CodexAppServer
from .audit import AuditStore
from .engine import MigrationEngine, MigrationError
from .fleet import FleetError, HostFleet
from .repository import CodexRepository, RepositoryError
from .server import CodexTransferServer


def _default(value: Any, suppress: bool) -> Any:
    return argparse.SUPPRESS if suppress else value


def _add_storage_options(target: argparse.ArgumentParser, suppress: bool = False) -> None:
    target.add_argument(
        "--codex-home",
        type=Path,
        default=_default(Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser(), suppress),
        help="Codex data directory (default: CODEX_HOME or ~/.codex)",
    )
    target.add_argument(
        "--data-dir",
        type=Path,
        default=_default(Path("~/.codex-transfer").expanduser(), suppress),
        help="Audit and backup directory",
    )
    target.add_argument(
        "--codex-bin",
        default=_default("codex", suppress),
        help="Codex CLI executable for Fork and archive operations",
    )


def _add_server_options(target: argparse.ArgumentParser, suppress: bool = False) -> None:
    target.add_argument(
        "--host",
        default=_default("127.0.0.1", suppress),
        choices=["127.0.0.1", "localhost"],
    )
    target.add_argument("--port", type=int, default=_default(8765, suppress))


def _add_output_option(target: argparse.ArgumentParser) -> None:
    target.add_argument("--json", action="store_true", help="Emit stable JSON output")


def _add_command_storage_options(target: argparse.ArgumentParser) -> None:
    _add_storage_options(target, suppress=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="codex-transfer",
        description="Inspect, fork, move, archive, and restore Codex sessions across providers and hosts",
    )
    _add_storage_options(result)
    _add_server_options(result)
    subparsers = result.add_subparsers(dest="command", metavar="COMMAND")

    serve = subparsers.add_parser("serve", help="Start the local web workbench")
    _add_storage_options(serve, suppress=True)
    _add_server_options(serve, suppress=True)

    status = subparsers.add_parser("status", help="Inspect storage and audit health")
    _add_command_storage_options(status)
    _add_output_option(status)

    hosts = subparsers.add_parser("hosts", help="List local and active Codex Desktop SSH hosts")
    _add_command_storage_options(hosts)
    _add_output_option(hosts)

    sessions = subparsers.add_parser("sessions", help="List and filter sessions by host")
    _add_command_storage_options(sessions)
    sessions.add_argument("--provider", help="Only sessions in this provider bucket")
    sessions.add_argument("--host", default="all", help="Host ID, or all (default)")
    sessions.add_argument("--project", help="Only sessions whose cwd exactly matches this path")
    sessions.add_argument(
        "--status",
        choices=["all", "ready", "locked", "archived"],
        default="all",
        help="Session status filter",
    )
    sessions.add_argument("--search", default="", help="Search title, cwd, ID, or model")
    sessions.add_argument(
        "--sort", choices=["newest", "oldest", "title", "size"], default="newest"
    )
    _add_output_option(sessions)

    operations = subparsers.add_parser("operations", help="List audited operations")
    _add_command_storage_options(operations)
    operations.add_argument("--limit", type=int, default=0, help="Maximum rows; 0 means all")
    _add_output_option(operations)

    fork_preview = subparsers.add_parser(
        "fork-preview", help="Preflight one or more provider forks"
    )
    _add_command_storage_options(fork_preview)
    fork_preview.add_argument("--session", action="append", required=True, dest="session_ids")
    fork_preview.add_argument("--target", required=True, dest="target_provider")
    fork_preview.add_argument("--source-host", default="local")
    fork_preview.add_argument("--target-host", default="local")
    fork_preview.add_argument("--target-cwd", default="")
    _add_output_option(fork_preview)

    fork = subparsers.add_parser("fork", help="Back up and fork one or more sessions")
    _add_command_storage_options(fork)
    fork.add_argument("--session", action="append", required=True, dest="session_ids")
    fork.add_argument("--target", required=True, dest="target_provider")
    fork.add_argument("--source-host", default="local")
    fork.add_argument("--target-host", default="local")
    fork.add_argument("--target-cwd", default="")
    fork.add_argument("--acknowledge", required=True, choices=["FORK"])
    _add_output_option(fork)

    move_preview = subparsers.add_parser("move-preview", help="Preflight a provider move")
    _add_command_storage_options(move_preview)
    move_preview.add_argument("--session", action="append", required=True, dest="session_ids")
    move_preview.add_argument("--source", required=True, dest="source_provider")
    move_preview.add_argument("--target", required=True, dest="target_provider")
    move_preview.add_argument("--source-host", default="local")
    move_preview.add_argument("--target-host", default="local")
    move_preview.add_argument("--target-cwd", default="")
    _add_output_option(move_preview)

    move = subparsers.add_parser("move", help="Back up and move original sessions")
    _add_command_storage_options(move)
    move.add_argument("--session", action="append", required=True, dest="session_ids")
    move.add_argument("--source", required=True, dest="source_provider")
    move.add_argument("--target", required=True, dest="target_provider")
    move.add_argument("--source-host", default="local")
    move.add_argument("--target-host", default="local")
    move.add_argument("--target-cwd", default="")
    move.add_argument("--acknowledge", required=True, choices=["MIGRATE"])
    _add_output_option(move)

    for command, archived, preview in (
        ("archive-preview", True, True),
        ("archive", True, False),
        ("unarchive-preview", False, True),
        ("unarchive", False, False),
    ):
        action = "Archive" if archived else "Unarchive"
        command_parser = subparsers.add_parser(
            command,
            help=f"{'Preflight' if preview else 'Back up and execute'} {action.lower()} for sessions",
        )
        _add_command_storage_options(command_parser)
        command_parser.add_argument(
            "--session", action="append", required=True, dest="session_ids"
        )
        if not preview:
            command_parser.add_argument(
                "--acknowledge",
                required=True,
                choices=["ARCHIVE" if archived else "UNARCHIVE"],
            )
        _add_output_option(command_parser)

    restore_preview = subparsers.add_parser(
        "restore-preview", help="Check whether an operation can be safely restored"
    )
    _add_command_storage_options(restore_preview)
    restore_preview.add_argument("--operation", required=True, dest="operation_id")
    _add_output_option(restore_preview)

    restore = subparsers.add_parser("restore", help="Restore or undo an audited operation")
    _add_command_storage_options(restore)
    restore.add_argument("--operation", required=True, dest="operation_id")
    restore.add_argument("--acknowledge", required=True, choices=["RESTORE"])
    _add_output_option(restore)
    return result


def build_engine(args: argparse.Namespace) -> MigrationEngine:
    repository = CodexRepository(args.codex_home)
    audit = AuditStore(args.data_dir)
    app_server = CodexAppServer(repository.home, executable=args.codex_bin)
    fleet = HostFleet(repository, audit, app_server)
    return MigrationEngine(
        repository,
        audit,
        app_server,
        app_server,
        fleet,
    )


def _serve(args: argparse.Namespace, engine: MigrationEngine) -> None:
    server = CodexTransferServer((args.host, args.port), engine)
    print(f"Codex Transfer: http://{args.host}:{server.server_port}")
    print(f"Codex home: {engine.repository.home}")
    print(f"Backups: {engine.audit.root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()


def _filtered_sessions(args: argparse.Namespace, engine: MigrationEngine) -> list[dict[str, Any]]:
    sessions = (
        engine.workspace_snapshot(wait_for_remote=True)["sessions"]
        if hasattr(engine, "workspace_snapshot")
        else [session.to_summary_dict() for session in engine.repository.scan_sessions()]
    )
    query = args.search.casefold().strip()

    def included(session: dict[str, Any]) -> bool:
        if getattr(args, "host", "all") != "all" and session.get("host_id", "local") != args.host:
            return False
        if args.provider and session["provider"] != args.provider:
            return False
        if args.project is not None and session["cwd"] != args.project:
            return False
        if args.status == "ready" and (session["locked"] or session["archived"]):
            return False
        if args.status == "locked" and not session["locked"]:
            return False
        if args.status == "archived" and not session["archived"]:
            return False
        haystack = " ".join(
            str(session.get(key) or "") for key in ("title", "cwd", "id", "model")
        ).casefold()
        return not query or query in haystack

    result = [session for session in sessions if included(session)]
    if args.sort == "oldest":
        result.sort(key=lambda item: item["updated_at"])
    elif args.sort == "title":
        result.sort(key=lambda item: item["title"].casefold())
    elif args.sort == "size":
        result.sort(key=lambda item: item["size_bytes"], reverse=True)
    else:
        result.sort(key=lambda item: item["updated_at"], reverse=True)
    return result


def _fork_batch(args: argparse.Namespace, engine: MigrationEngine) -> tuple[dict[str, Any], int]:
    if args.source_host != "local" or args.target_host != "local":
        result = engine.transfer(
            args.session_ids,
            args.source_host,
            args.target_host,
            args.target_provider,
            args.target_cwd,
            False,
            args.acknowledge,
        )
        return result, int(result["failed"] is not None)
    plan = engine.preview_forks(args.session_ids, args.target_provider)
    if not plan.executable:
        raise MigrationError("Preflight has critical risks; fork batch was not started")
    completed = []
    failed = None
    for session_id in args.session_ids:
        try:
            completed.append(engine.fork(session_id, args.target_provider, args.acknowledge))
        except (MigrationError, RepositoryError, AppServerError, OSError, ValueError) as exc:
            failed = {"session_id": session_id, "error": str(exc)}
            break
    return {
        "requested_session_ids": args.session_ids,
        "completed": completed,
        "failed": failed,
        "batch_atomic": False,
    }, int(failed is not None)


def execute_command(args: argparse.Namespace, engine: MigrationEngine) -> tuple[Any, int]:
    if args.command == "status":
        return engine.status(), 0
    if args.command == "hosts":
        hosts = engine.workspace_snapshot(wait_for_remote=True).get("hosts", [])
        return {"count": len(hosts), "hosts": hosts}, 0
    if args.command == "sessions":
        sessions = _filtered_sessions(args, engine)
        return {"count": len(sessions), "sessions": sessions}, 0
    if args.command == "operations":
        operations = engine.audit.list_operations()
        if args.limit > 0:
            operations = operations[: args.limit]
        return {"count": len(operations), "operations": operations}, 0
    if args.command == "fork-preview":
        if args.source_host != "local" or args.target_host != "local":
            return engine.preview_transfer(
                args.session_ids,
                args.source_host,
                args.target_host,
                args.target_provider,
                args.target_cwd,
                False,
            ), 0
        return engine.preview_forks(args.session_ids, args.target_provider).to_dict(), 0
    if args.command == "fork":
        return _fork_batch(args, engine)
    if args.command == "move-preview":
        if args.source_host != "local" or args.target_host != "local":
            return engine.preview_transfer(
                args.session_ids,
                args.source_host,
                args.target_host,
                args.target_provider,
                args.target_cwd,
                True,
            ), 0
        return engine.preview(
            args.session_ids, args.source_provider, args.target_provider
        ).to_dict(), 0
    if args.command == "move":
        if args.source_host != "local" or args.target_host != "local":
            result = engine.transfer(
                args.session_ids,
                args.source_host,
                args.target_host,
                args.target_provider,
                args.target_cwd,
                True,
                args.acknowledge,
            )
            return result, int(result["failed"] is not None)
        return engine.execute(
            args.session_ids,
            args.source_provider,
            args.target_provider,
            args.acknowledge,
        ), 0
    if args.command in {"archive-preview", "unarchive-preview"}:
        return engine.preview_archive(args.session_ids, args.command == "archive-preview"), 0
    if args.command in {"archive", "unarchive"}:
        result = engine.set_archived_batch(
            args.session_ids, args.command == "archive", args.acknowledge
        )
        return result, int(result["failed"] is not None)
    if args.command == "restore-preview":
        return engine.preview_restore(args.operation_id), 0
    if args.command == "restore":
        return engine.restore(args.operation_id, args.acknowledge), 0
    raise ValueError(f"Unknown command: {args.command}")


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _format_time(value: int) -> str:
    try:
        return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return str(value)


def _line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _print_status(data: dict[str, Any], out: TextIO) -> None:
    print(f"Codex home: {data['codex_home']}", file=out)
    print(f"Backups:    {data['data_dir']}", file=out)
    print(
        f"Sessions:   {data['session_count']} ({data['locked_session_count']} locked)",
        file=out,
    )
    print(f"Providers:  {', '.join(data['providers'])}", file=out)
    print(f"Audit:      {'valid' if data['audit_chain_valid'] else 'INVALID'}", file=out)
    for database in data["databases"]:
        print(
            f"Database:   {database['path']} [{database['integrity']}, "
            f"{_format_size(database['size_bytes'])}]",
            file=out,
        )


def _print_sessions(data: dict[str, Any], out: TextIO) -> None:
    print(f"{data['count']} session(s)", file=out)
    for session in data["sessions"]:
        status = "locked" if session["locked"] else "archived" if session["archived"] else "ready"
        print(
            f"{session['id']}  {session['provider']}  {status}  "
            f"{_format_time(session['updated_at'])}  {_format_size(session['size_bytes'])}",
            file=out,
        )
        print(f"  {_line(session['title'], 100)}", file=out)
        print(
            f"  project={_line(session['cwd'] or '(none)', 120)} model={session['model'] or '-'}",
            file=out,
        )


def _print_operations(data: dict[str, Any], out: TextIO) -> None:
    print(f"{data['count']} operation(s)", file=out)
    for operation in data["operations"]:
        session_ids = operation.get("session_ids") or operation.get("forked_session_ids") or []
        print(
            f"{operation.get('operation_id', '-')}  {operation.get('kind', '-')}  "
            f"{operation.get('status', '-')}  sessions={len(session_ids)}",
            file=out,
        )


def _print_hosts(data: dict[str, Any], out: TextIO) -> None:
    print(f"{data['count']} host(s)", file=out)
    for host in data["hosts"]:
        state = "connected" if host.get("connected") else "unavailable"
        print(
            f"{host['id']}  {host.get('kind', '-')}  {state}  "
            f"sessions={host.get('session_count', 0)}  providers={','.join(host.get('providers', []))}",
            file=out,
        )
        if host.get("error"):
            print(f"  {host['error']}", file=out)


def _print_plan(data: dict[str, Any], out: TextIO) -> None:
    print(
        f"Preflight: {'executable' if data.get('executable') else 'BLOCKED'}; "
        f"sessions={len(data.get('sessions') or data.get('session_ids') or [])}",
        file=out,
    )
    if "estimated_backup_bytes" in data:
        print(f"Estimated backup: {_format_size(data['estimated_backup_bytes'])}", file=out)
    for risk in data.get("risks", []):
        print(f"[{risk['severity'].upper()}] {risk['code']}: {risk['message']}", file=out)
        print(f"  {risk['remediation']}", file=out)


def _print_result(command: str, data: Any, out: TextIO) -> None:
    if command == "status":
        _print_status(data, out)
    elif command == "sessions":
        _print_sessions(data, out)
    elif command == "hosts":
        _print_hosts(data, out)
    elif command == "operations":
        _print_operations(data, out)
    elif command.endswith("-preview"):
        _print_plan(data, out)
    elif command in {"fork", "archive", "unarchive"}:
        verb = {"fork": "Forked", "archive": "Archived", "unarchive": "Unarchived"}[command]
        print(
            f"{verb} {len(data['completed'])}/{len(data['requested_session_ids'])} session(s).",
            file=out,
        )
        for result in data["completed"]:
            detail = (
                f" fork={result['forked_session_ids'][0]}"
                if command == "fork"
                else ""
            )
            print(f"  operation={result['operation_id']}{detail}", file=out)
        if data["failed"]:
            print(
                f"  failed={data['failed']['session_id']}: {data['failed']['error']}",
                file=out,
            )
    else:
        print(
            f"Completed {data.get('kind', command)} operation {data.get('operation_id', '-')}",
            file=out,
        )
        print(f"Status: {data.get('status', 'completed')}", file=out)


def main(
    argv: Sequence[str] | None = None,
    engine_factory: Callable[[argparse.Namespace], MigrationEngine] = build_engine,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = parser().parse_args(argv)
    try:
        engine = engine_factory(args)
        if args.command in {None, "serve"}:
            _serve(args, engine)
            return 0
        data, exit_code = execute_command(args, engine)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), file=stdout)
        else:
            _print_result(args.command, data, stdout)
        return exit_code
    except (MigrationError, FleetError, RepositoryError, AppServerError, OSError, ValueError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=stderr)
        else:
            print(f"error: {exc}", file=stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
