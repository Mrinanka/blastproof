"""Admin commands, run from backend/:

    uv run python -m app.cli create-user --email you@example.com --display-name "You" --role admin
    uv run python -m app.cli create-agent --owner-email you@example.com --name claude-code
    uv run python -m app.cli preview-sql --sql "DELETE FROM ..." --confirm-project <project name>
    uv run python -m app.cli knowledge-scan
    uv run python -m app.cli knowledge-ingest --file ../demo_company/schema_docs.md [--tables a,b]
    uv run python -m app.cli reindex

The first two connect as blastproof_app (BLASTPROOF_DATABASE_URL). A password is never a
command-line argument (it would land in shell history): create-user asks for it, or reads one
line from stdin with --password-stdin. create-agent prints the new key once; only its hash is
stored.
preview-sql is the one manual Neon run (step 10): it makes a real branch of the Neon project, runs
the statement there in a rolled-back transaction and prints counts and timings only (no rows, no
SQL). It refuses unless the project's name equals --confirm-project.
The knowledge commands (step 11) fill and rebuild the knowledge base: knowledge-scan reads the
company database's schema, knowledge-ingest splits one Markdown file into chunks (the source is
the file name unless --source is given), reindex recomputes every vector. They print counts only.
Errors print one line and no traceback, so no URL or secret reaches the terminal.
"""

import argparse
import asyncio
import getpass
import sys
import time
import uuid
from pathlib import Path

from app.config import Settings
from app.core.clock import SystemClock
from app.db.enums import UserRole
from app.db.session import WrongAppRoleError, make_engine, make_sessionmaker
from app.logging import configure_logging
from app.services import auth as auth_service
from app.services.classifier import Kind, classify
from app.services.embeddings import build_embedder
from app.services.knowledge import DocumentError, KnowledgeService
from app.services.neon import NeonApiClient, neon_client_for
from app.services.policy import load_policy
from app.services.shadow import ShadowBusyError, ShadowFailedError, ShadowRunner
from app.services.vectorstores import build_vector_store


class CliError(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Blastproof admin")
    commands = parser.add_subparsers(dest="command", required=True)

    user = commands.add_parser("create-user", help="Create a dashboard user")
    user.add_argument("--email", required=True)
    user.add_argument("--display-name", required=True)
    user.add_argument("--role", choices=[r.value for r in UserRole], default=UserRole.MEMBER.value)
    user.add_argument(
        "--password-stdin", action="store_true", help="Read the password from one line of stdin"
    )

    agent = commands.add_parser("create-agent", help="Create an agent and print its key once")
    agent.add_argument("--owner-email", required=True)
    agent.add_argument("--name", required=True)

    preview = commands.add_parser("preview-sql", help="Preview one write on a real Neon branch")
    preview.add_argument("--sql", required=True)
    preview.add_argument(
        "--confirm-project", required=True, help="The Neon project's name; it must match exactly"
    )

    commands.add_parser("knowledge-scan", help="Read the company schema into the knowledge base")
    ingest = commands.add_parser("knowledge-ingest", help="Add one Markdown file as knowledge")
    ingest.add_argument("--file", required=True, type=Path)
    ingest.add_argument("--source", help="Name of the source (default: the file name)")
    ingest.add_argument("--tables", default="", help="Comma-separated tables to tag every chunk")
    commands.add_parser("reindex", help="Recompute every vector from the stored chunk text")
    return parser


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise CliError("The passwords do not match.")
    return password


async def _run(args: argparse.Namespace, database_url: str, password: str | None) -> str:
    engine = make_engine(database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            if args.command == "create-user":
                assert password is not None
                user = await auth_service.create_user(
                    session,
                    email=args.email,
                    display_name=args.display_name,
                    role=UserRole(args.role),
                    password=password,
                )
                return f"User created: {user.email} (id {user.id}, role {user.role})"
            created = await auth_service.create_agent(
                session, owner_email=args.owner_email, name=args.name
            )
            return (
                f"Agent created: {created.agent.name} (id {created.agent.id})\n"
                "API key (shown once; store it now, it cannot be shown again):\n"
                f"{created.key.key}"
            )
    finally:
        await engine.dispose()


async def _preview(settings: Settings, sql: str, confirm_project: str) -> str:
    """Counts and timings of one write on a real branch; never rows, SQL or secrets."""
    if settings.company_connection_profile != "neon" or settings.neon_client != "api":
        raise CliError("preview-sql needs COMPANY_CONNECTION_PROFILE=neon and NEON_CLIENT=api.")
    clock = SystemClock()
    client = neon_client_for("neon", settings, clock)
    if not isinstance(client, NeonApiClient):
        raise CliError("Neon is not configured (API key, project, parent branch, writer URL).")
    try:
        name = await client.project_name()
        if name != confirm_project:
            raise CliError(f"The Neon project is not named {confirm_project!r}; refusing to run.")
        classification = classify(sql, now=clock.now())
        if classification.kind != Kind.WRITE:
            raise CliError(f"That is not a write (classified as {classification.kind.value}).")
        runner = ShadowRunner(
            policy=load_policy(settings.policy_file),
            clients={"neon": client},
            max_concurrent=1,
            timeout_seconds=settings.shadow_timeout_seconds,
        )
        result = await runner.run(
            action_id=uuid.uuid4(), profile="neon", classification=classification
        )
    except (ShadowBusyError, ShadowFailedError) as exc:
        raise CliError(f"The preview failed: {exc.reason_code}.") from None
    finally:
        await client.aclose()
    lines = [
        f"Project: {name}",
        f"Preview took {result.preview_ms} ms (targets: 20000 typical, 60000 mass delete)",
    ]
    for table, t in result.diff.tables.items():
        if t.changed:
            lines.append(
                f"{table}: deleted {t.deleted}, updated {t.updated}, inserted {t.inserted}"
                f" (of {t.rows_before}){' [cascade]' if t.is_cascade else ''}"
            )
    for key, count in result.diff.derived_groups.items():
        lines.append(f"{key}: {count}")
    lines.append(f"Row changes recorded: {len(result.diff.changes)}")
    return chr(10).join(lines)


async def _knowledge(settings: Settings, args: argparse.Namespace) -> str:
    """Counts only: no chunk text, file content, URL or key reaches the terminal."""
    url = settings.blastproof_database_url
    if url is None:
        raise CliError(
            "BLASTPROOF_DATABASE_URL is not set. See backend/.env.example for the local value."
        )
    engine = make_engine(url.get_secret_value())
    try:
        sessionmaker = make_sessionmaker(engine)
        store = build_vector_store(settings, sessionmaker)
        assert store is not None
        service = KnowledgeService(sessionmaker, build_embedder(settings), store)
        started = time.perf_counter()
        if args.command == "knowledge-scan":
            count = await service.scan(settings)
            what = f"Scanned the company schema: {count} table chunks"
        elif args.command == "knowledge-ingest":
            try:
                data = args.file.read_bytes()
            except OSError:
                raise CliError("The file could not be read.") from None
            tables = [t for t in args.tables.split(",") if t.strip()]
            count = await service.ingest(args.source or args.file.name, data, tables)
            what = f"Ingested {count} chunks"
        else:
            count = await service.reindex()
            what = f"Reindexed {count} chunks"
        return f"{what} in {round(time.perf_counter() - started, 1)} s."
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()
    # Logs go to stderr, so stdout holds only the result (and the key, for create-agent).
    configure_logging(settings.log_level, stream=sys.stderr)
    try:
        if args.command == "preview-sql":
            print(asyncio.run(_preview(settings, args.sql, args.confirm_project)))
            return 0
        if args.command in ("knowledge-scan", "knowledge-ingest", "reindex"):
            print(asyncio.run(_knowledge(settings, args)))
            return 0
        if settings.blastproof_database_url is None:
            raise CliError(
                "BLASTPROOF_DATABASE_URL is not set. See backend/.env.example for the local value."
            )
        password = _read_password(args.password_stdin) if args.command == "create-user" else None
        print(
            asyncio.run(_run(args, settings.blastproof_database_url.get_secret_value(), password))
        )
    except (CliError, DocumentError, auth_service.AccountError, WrongAppRoleError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # one line, never the message: it may hold a URL
        print(f"Error: the command failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
