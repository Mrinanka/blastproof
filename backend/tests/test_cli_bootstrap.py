"""The `bootstrap` command: arguments, the password, the output and one-line errors (no Docker)."""

import io
from pathlib import Path

import pytest

from app import cli
from app.config import Settings
from app.services.bootstrap import BootstrapError, BootstrapReport

KEY = "bp_agent_secretkeyvalue"


def report(*, created: bool = True) -> BootstrapReport:
    return BootstrapReport(
        admin="created" if created else "existing",
        admin_email="me@example.com",
        agent="created" if created else "existing",
        agent_name="claude-code",
        agent_key_prefix="bp_agent_secr",
        agent_key=KEY if created else None,
        setup=(
            (
                "http://localhost:8000/mcp",
                f"claude mcp add ... Bearer {KEY}",
                f"BLASTPROOF_AGENT_KEY={KEY}",
            )
            if created
            else None
        ),
        system_name="company",
        system_profile="local",
        scan_chunks=11,
        docs_chunks=13,
    )


def fake_bootstrap(result: BootstrapReport | Exception, calls: list[dict]):  # type: ignore[no-untyped-def]
    async def bootstrap(settings: Settings, **kwargs: object) -> BootstrapReport:
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    return bootstrap


def run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(cli, "Settings", lambda: Settings(_env_file=None, app_env="test"))
    return cli.main(["bootstrap", "--admin-email", "me@example.com", "--admin-name", "Me", *argv])


def test_the_arguments_are_parsed_with_defaults() -> None:
    args = cli._parser().parse_args(
        ["bootstrap", "--admin-email", "a@b.co", "--admin-name", "A", "--password-stdin"]
    )
    assert args.agent_name == "claude-code" and args.password_stdin
    assert args.schema_docs == cli.DEFAULT_SCHEMA_DOCS
    assert args.schema_docs.name == "schema_docs.md" and args.schema_docs.is_file()


def test_the_admin_email_and_name_are_required() -> None:
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["bootstrap"])


def test_a_created_agent_prints_its_key_and_the_setup_lines_once(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict] = []
    monkeypatch.setattr(cli, "bootstrap", fake_bootstrap(report(), calls))
    assert run(monkeypatch, "--agent-name", "mine", "--schema-docs", "x.md") == 0
    out = capsys.readouterr().out
    assert out.count(KEY) == 3  # the key line, the `claude mcp add` line, the hook line
    assert "shown once" in out and "11 schema chunks, 13 schema docs chunks" in out
    assert calls[0]["agent_name"] == "mine" and calls[0]["schema_docs"] == Path("x.md")


def test_an_existing_agent_prints_no_key(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cli, "bootstrap", fake_bootstrap(report(created=False), []))
    assert run(monkeypatch) == 0
    out = capsys.readouterr().out
    assert KEY not in out and "bp_agent_secr" in out and "create-agent" in out


def test_the_password_is_read_lazily_from_stdin(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict] = []
    monkeypatch.setattr(cli, "bootstrap", fake_bootstrap(report(), calls))
    monkeypatch.setattr("sys.stdin", io.StringIO("a-long-enough-password\nextra\n"))
    assert run(monkeypatch, "--password-stdin") == 0
    assert calls[0]["password"]() == "a-long-enough-password"


def test_a_mismatched_password_repeat_is_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    answers = iter(["one-long-password", "other-long-password"])
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": next(answers))
    with pytest.raises(cli.CliError, match="do not match"):
        cli._read_password(False)


def test_a_bootstrap_error_prints_one_line_and_no_traceback(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cli, "bootstrap", fake_bootstrap(BootstrapError("Nope, safe text."), []))
    assert run(monkeypatch) == 1
    captured = capsys.readouterr()
    assert captured.err == "Error: Nope, safe text.\n" and captured.out == ""


def test_an_unexpected_crash_names_only_its_class(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    boom = RuntimeError("postgresql://user:hunter2@host/db")
    monkeypatch.setattr(cli, "bootstrap", fake_bootstrap(boom, []))
    assert run(monkeypatch) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "hunter2" not in err and "Traceback" not in err
