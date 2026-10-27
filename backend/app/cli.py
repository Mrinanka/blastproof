"""Admin commands, run from backend/:

    uv run python -m app.cli create-user --email you@example.com --display-name "You" --role admin
    uv run python -m app.cli create-agent --owner-email you@example.com --name claude-code
    uv run python -m app.cli preview-sql --sql "DELETE FROM ..." --confirm-project <project name>
    uv run python -m app.cli knowledge-scan
    uv run python -m app.cli knowledge-ingest --file ../demo_company/schema_docs.md [--tables a,b]
    uv run python -m app.cli reindex
    uv run --env-file .env python -m app.cli impact-report --sql "DELETE ..." --intent "..."
    uv run --env-file .env python -m app.cli judge-report --sql "DELETE ..." --intent "..."
    uv run python -m app.cli seed-demo-actions [--add-pending] [--password-stdin]

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
impact-report (step 12) previews a write on the local profile (a clone of the Compose company_main),
retrieves knowledge and calls SUMMARY_MODEL for real (it costs about a cent). It prints the report,
where it came from, the tokens and the cost; never the prompt. `--env-file .env` puts the model's
key (GOOGLE_API_KEY) into the environment, where LangChain reads it.
judge-report (step 13) does the same preview, retrieval and summary, then calls JUDGE_MODEL for
real and prints the verdict, the final decision and the cost. It does not call the judge when the
rules already block.
seed-demo-actions (step 20, pulled forward) creates two demo users with an agent each and runs real
writes for both through the real graph: real shadow runs on the local company database, real
retrieval, scripted models and the stub commit. It leaves actions in every status, two of them
paused for review, so /docs can approve and reject them. --add-pending adds one more pending
action for demo user 1. The password is asked only when a demo user has to be created. It prints
one line per action (status, owner, id): never SQL, rows, URLs or keys. Local only.
Errors print one line and no traceback, so no URL or secret reaches the terminal.
"""

import argparse
import asyncio
import getpass
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.core.clock import Clock, FrozenClock, SystemClock
from app.db.enums import Decision, UserRole
from app.db.session import WrongAppRoleError, make_engine, make_sessionmaker
from app.logging import configure_logging
from app.services import auth as auth_service
from app.services.classifier import Classification, Kind, classify
from app.services.demo_seed import SeedError, seed_demo_actions
from app.services.embeddings import build_embedder
from app.services.impact import ImpactEngine, ImpactResult
from app.services.judge import (
    JudgeConfigError,
    JudgeContext,
    assess_safely,
    build_judge,
    decide,
    injection_signals,
)
from app.services.knowledge import DocumentError, KnowledgeService, Retrieval, RetrievalError
from app.services.llm import LlmConfigError
from app.services.neon import NeonApiClient, neon_client_for
from app.services.policy import Policy, evaluate_pass_two, load_policy
from app.services.shadow import ShadowBusyError, ShadowFailedError, ShadowResult, ShadowRunner
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

    impact = commands.add_parser(
        "impact-report", help="Preview one write locally and summarize it with SUMMARY_MODEL"
    )
    impact.add_argument("--sql", required=True)
    impact.add_argument("--intent", required=True)
    impact.add_argument(
        "--now", help="Pin now() to this ISO time, e.g. 2026-10-01T00:00:00+00:00 (the seed anchor)"
    )

    judge = commands.add_parser(
        "judge-report", help="Preview one write locally, summarize it and judge it with JUDGE_MODEL"
    )
    judge.add_argument("--sql", required=True)
    judge.add_argument("--intent", required=True)
    judge.add_argument("--now", help="Pin now() to this ISO time, as for impact-report")

    seed = commands.add_parser(
        "seed-demo-actions", help="Create demo users and actions in every status (local only)"
    )
    seed.add_argument("--add-pending", action="store_true", help="Add one pending action only")
    seed.add_argument(
        "--password-stdin", action="store_true", help="Read the demo password from one stdin line"
    )
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


def _clock(now: str | None) -> Clock:
    if now is None:
        return SystemClock()
    try:
        at = datetime.fromisoformat(now)
    except ValueError:
        raise CliError("--now must be an ISO time such as 2026-10-01T00:00:00+00:00.") from None
    return FrozenClock(at if at.tzinfo else at.replace(tzinfo=UTC))


async def _retrieve(settings: Settings, intent: str, tables: Sequence[str]) -> Retrieval | None:
    """Knowledge from Blastproof's database, or None when it is not set up (the summary then runs
    without knowledge, as it would after a retrieval failure)."""
    url = settings.blastproof_database_url
    if url is None:
        return None
    engine = make_engine(url.get_secret_value())
    try:
        sessionmaker = make_sessionmaker(engine)
        store = build_vector_store(settings, sessionmaker)
        assert store is not None
        service = KnowledgeService(sessionmaker, build_embedder(settings), store)
        try:
            return await service.retrieve(intent, tables)
        except RetrievalError:
            return None
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class _LocalRun:
    clock: Clock
    policy: Policy
    classification: Classification
    preview: ShadowResult
    retrieval: Retrieval | None
    summary: ImpactResult


async def _local_run(settings: Settings, args: argparse.Namespace) -> _LocalRun:
    """A local-profile preview, retrieval and the summary: the inputs both reports start from."""
    clock = _clock(args.now)
    client = neon_client_for("local", settings, clock)
    if client is None:
        raise CliError(
            f"{args.command} previews on the local profile: set LOCAL_COMPANY_WRITER_URL and"
            " FAKE_NEON_ADMIN_URL (see backend/.env.example)."
        )
    classification = classify(args.sql, now=clock.now())
    if classification.kind != Kind.WRITE:
        raise CliError(f"That is not a write (classified as {classification.kind.value}).")
    policy = load_policy(settings.policy_file)
    runner = ShadowRunner(
        policy=policy,
        clients={"local": client},
        max_concurrent=1,
        timeout_seconds=settings.shadow_timeout_seconds,
    )
    try:
        preview = await runner.run(
            action_id=uuid.uuid4(), profile="local", classification=classification
        )
    except (ShadowBusyError, ShadowFailedError) as exc:
        raise CliError(f"The preview failed: {exc.reason_code}.") from None
    retrieval = await _retrieve(settings, args.intent, classification.tables)
    summary = await ImpactEngine(settings, policy).summarize(
        action_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        intent=args.intent,
        classification=classification,
        diff=preview.diff,
        retrieval=retrieval,
    )
    return _LocalRun(clock, policy, classification, preview, retrieval, summary)


def _knowledge_line(retrieval: Retrieval | None) -> str:
    return "Knowledge chunks: " + (
        "none (no database or retrieval failed)"
        if retrieval is None
        else str(len(retrieval.chunks))
    )


async def _impact_report(settings: Settings, args: argparse.Namespace) -> str:
    """A local-profile preview, retrieval and the summary. Prints the report and its cost; never
    the prompt, rows, URLs or keys."""
    run = await _local_run(settings, args)
    result = run.summary
    report = result.report
    lines = [f"Headline: {report.headline}", "Key points:"]
    lines += [f"  - {p}" for p in report.key_points]
    lines.append("Concerns:")
    lines += [f"  - {c}" for c in report.concerns]
    lines += [
        f"Source: {result.source} (fallback reason: {result.fallback_reason or 'none'})",
        f"Model: {settings.summary_model}",
        f"Tokens: {result.input_tokens} in, {result.output_tokens} out;"
        f" cost {result.cost_usd} USD; summary took {result.duration_ms} ms",
        _knowledge_line(run.retrieval),
        f"Preview took {run.preview.preview_ms} ms",
    ]
    return chr(10).join(lines)


async def _judge_report(settings: Settings, args: argparse.Namespace) -> str:
    """The same inputs, then the real judge. Prints the verdict, the final decision and the cost;
    never the prompt, rows, URLs or keys."""
    run = await _local_run(settings, args)
    rules = evaluate_pass_two(run.policy, run.classification, run.preview.diff.facts())
    lines = [f"Rules decided: {rules.decision.value} ({rules.reason})"]
    context = JudgeContext(
        action_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        intent=args.intent,
        classification=run.classification,
        diff=run.preview.diff,
        facts=run.summary.facts,
        report=run.summary.report,
        rules=rules,
        retrieval=run.retrieval,
    )
    judged = None
    if rules.decision != Decision.BLOCK:
        judge = build_judge(settings, run.policy)
        judged = await assess_safely(judge, context, settings.llm_timeout_seconds + 5)
        if judged.verdict is not None:
            v = judged.verdict
            lines += [
                f"Judge: {v.recommendation}, risk {v.risk_level}, confidence {v.confidence}",
                f"Reason: {v.reason}"
                + (" (withheld by the number check)" if judged.reason_withheld else ""),
            ]
        else:
            lines.append(f"Judge failed: {judged.failure}")
        lines.append(
            f"Model: {settings.judge_model}; tokens {judged.input_tokens} in,"
            f" {judged.output_tokens} out; cost {judged.cost_usd} USD;"
            f" judge took {judged.duration_ms} ms"
        )
    else:
        lines.append("The rules block this write, so the judge was not called.")
    final = decide(
        rules, judged, injection_signals(context), min_confidence=settings.judge_min_confidence
    )
    lines += [
        f"Final decision: {final.decision.value} (decided by {final.deciding};"
        f" ai_unavailable {final.ai_unavailable})",
        f"Final reason: {final.reason}",
        f"Injection hits: {', '.join(f'{h.field}:{h.pattern}' for h in final.injection) or 'none'}",
        f"Summary source: {run.summary.source}",
        _knowledge_line(run.retrieval),
        f"Preview took {run.preview.preview_ms} ms",
    ]
    return chr(10).join(lines)


async def _seed(settings: Settings, args: argparse.Namespace) -> tuple[str, bool]:
    report = await seed_demo_actions(
        settings,
        password=lambda: _read_password(args.password_stdin),
        add_pending=args.add_pending,
    )
    lines = [f"{a.status:<16}{a.owner_email}  {a.action_id}" for a in report.actions]
    for a in report.actions:
        if not a.ok:
            lines.append(f"Unexpected: {a.key} ended {a.status}, expected {a.expected}.")
    lines.append(
        f"{report.knowledge_chunks} knowledge chunks"
        + ("" if report.knowledge_chunks else ": run knowledge-scan / knowledge-ingest")
    )
    return chr(10).join(lines), report.ok


def _run_seed(settings: Settings, args: argparse.Namespace) -> tuple[str, bool]:
    # psycopg's async mode (the checkpointer) cannot use Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(_seed(settings, args), loop_factory=loop_factory)


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
        if args.command == "impact-report":
            print(asyncio.run(_impact_report(settings, args)))
            return 0
        if args.command == "judge-report":
            print(asyncio.run(_judge_report(settings, args)))
            return 0
        if args.command == "seed-demo-actions":
            text, ok = _run_seed(settings, args)
            print(text)
            return 0 if ok else 1
        if settings.blastproof_database_url is None:
            raise CliError(
                "BLASTPROOF_DATABASE_URL is not set. See backend/.env.example for the local value."
            )
        password = _read_password(args.password_stdin) if args.command == "create-user" else None
        print(
            asyncio.run(_run(args, settings.blastproof_database_url.get_secret_value(), password))
        )
    except (
        CliError,
        DocumentError,
        auth_service.AccountError,
        WrongAppRoleError,
        LlmConfigError,
        JudgeConfigError,
        SeedError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # one line, never the message: it may hold a URL
        print(f"Error: the command failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
