"""`_cause`: what the log says about a wrapped database error (never its message)."""

from sqlalchemy.exc import DBAPIError

from app.workflow.graph import _cause


class _Driver(Exception):
    def __init__(self, message: str, sqlstate: str | None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def test_a_wrapped_database_error_gives_its_class_and_sqlstate_only() -> None:
    exc = DBAPIError("INSERT ... 'a@b.com'", None, _Driver("secret a@b.com", "22P05"))
    assert _cause(exc) == {"cause_class": "_Driver", "sqlstate": "22P05"}


def test_a_malformed_sqlstate_is_dropped() -> None:
    exc = DBAPIError("s", None, _Driver("m", "not-a-code"))
    assert _cause(exc) == {"cause_class": "_Driver"}


def test_the_chained_cause_is_used_when_there_is_no_orig() -> None:
    try:
        try:
            raise _Driver("m", "40001")
        except _Driver as inner:
            raise RuntimeError("outer") from inner
    except RuntimeError as exc:
        assert _cause(exc) == {"cause_class": "_Driver", "sqlstate": "40001"}


def test_a_plain_error_gives_nothing() -> None:
    assert _cause(ValueError("x")) == {}
