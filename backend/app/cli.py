"""Admin commands, run from backend/:

    uv run python -m app.cli create-user --email you@example.com --display-name "You" --role admin
    uv run python -m app.cli create-agent --owner-email you@example.com --name claude-code

Both connect as blastproof_app (BLASTPROOF_DATABASE_URL). A password is never a command-line
argument (it would land in shell history): create-user asks for it, or reads one line from stdin
with --password-stdin. create-agent prints the new key once; only its hash is stored.
Errors print one line and no traceback, so no URL or secret reaches the terminal.
"""

import argparse
import asyncio
import getpass
import sys

from app.config import Settings
from app.db.enums import UserRole
from app.db.session import WrongAppRoleError, make_engine, make_sessionmaker
from app.logging import configure_logging
from app.services import auth as auth_service


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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()
    # Logs go to stderr, so stdout holds only the result (and the key, for create-agent).
    configure_logging(settings.log_level, stream=sys.stderr)
    try:
        if settings.blastproof_database_url is None:
            raise CliError(
                "BLASTPROOF_DATABASE_URL is not set. See backend/.env.example for the local value."
            )
        password = _read_password(args.password_stdin) if args.command == "create-user" else None
        print(
            asyncio.run(_run(args, settings.blastproof_database_url.get_secret_value(), password))
        )
    except (CliError, auth_service.AccountError, WrongAppRoleError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # one line, never the message: it may hold a URL
        print(f"Error: the command failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
