"""The `seed-demo-actions` command: arguments, the exit code and the one-line errors (no Docker)."""

import uuid

import pytest

from app import cli
from app.config import Settings
from app.services.demo_seed import SeededAction, SeedError, SeedReport


def run(monkeypatch: pytest.MonkeyPatch, *argv: str, **settings: object) -> int:
    monkeypatch.setattr(
        cli, "Settings", lambda: Settings(_env_file=None, app_env="test", **settings)
    )
    return cli.main(["seed-demo-actions", *argv])


def fake_seed(report: SeedReport | Exception, calls: list[dict]):  # type: ignore[no-untyped-def]
    async def seed(settings: Settings, *, password, add_pending: bool = False, **_: object):  # type: ignore[no-untyped-def]
        calls.append({"add_pending": add_pending, "password": password})
        if isinstance(report, Exception):
            raise report
        return report

    return seed


def action(status: str, expected: str, key: str = "k") -> SeededAction:
    return SeededAction(key, "demo-user-1@example.com", uuid.uuid4(), status, expected)


def test_the_arguments_are_parsed() -> None:
    args = cli._parser().parse_args(["seed-demo-actions", "--add-pending", "--password-stdin"])
    assert args.add_pending and args.password_stdin
    args = cli._parser().parse_args(["seed-demo-actions"])
    assert not args.add_pending and not args.password_stdin


def test_one_line_per_action_and_exit_zero(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict] = []
    report = SeedReport([action("committed", "committed"), action("expired", "expired")], 0)
    monkeypatch.setattr(cli, "seed_demo_actions", fake_seed(report, calls))
    assert run(monkeypatch, "--add-pending") == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in out[:2]] == ["committed", "expired"]
    assert "0 knowledge chunks: run knowledge-scan / knowledge-ingest" in out[-1]
    assert calls[0]["add_pending"] is True


def test_the_password_is_read_from_stdin_only_when_asked_for(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    import io

    calls: list[dict] = []
    monkeypatch.setattr(cli, "seed_demo_actions", fake_seed(SeedReport([], 3), calls))
    monkeypatch.setattr("sys.stdin", io.StringIO("a-long-enough-password\nextra\n"))
    assert run(monkeypatch, "--password-stdin") == 0
    assert calls[0]["password"]() == "a-long-enough-password"  # read lazily, on the call
    assert "3 knowledge chunks" in capsys.readouterr().out


def test_an_unexpected_status_is_reported_and_exits_one(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    report = SeedReport([action("awaiting_review", "committed", "u2-committed-auto")], 5)
    monkeypatch.setattr(cli, "seed_demo_actions", fake_seed(report, []))
    assert run(monkeypatch) == 1
    assert "Unexpected: u2-committed-auto ended awaiting_review, expected committed." in (
        capsys.readouterr().out
    )


def test_a_seed_error_prints_one_line_and_no_traceback(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cli, "seed_demo_actions", fake_seed(SeedError("Nothing to see."), []))
    assert run(monkeypatch) == 1
    captured = capsys.readouterr()
    assert captured.err == "Error: Nothing to see.\n" and captured.out == ""


def test_an_unexpected_crash_names_only_its_class(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    boom = RuntimeError("postgresql://user:secret@host/db")
    monkeypatch.setattr(cli, "seed_demo_actions", fake_seed(boom, []))
    assert run(monkeypatch) == 1
    err = capsys.readouterr().err
    assert err == "Error: the command failed (RuntimeError).\n" and "secret" not in err


def test_missing_urls_stop_before_anything_runs(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(monkeypatch) == 1  # no URLs set at all, and the real seed is not faked here
    assert "BLASTPROOF_DATABASE_URL" in capsys.readouterr().err
