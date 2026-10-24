"""The module-level `app` is built on first access, so importing app.main never reads .env."""

import importlib

import pytest
from fastapi import FastAPI

import app.main
from app.config import Settings


def test_import_does_not_build_app(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> Settings:
        raise AssertionError("get_settings() was called at import time")

    monkeypatch.setattr("app.config.get_settings", fail)
    try:
        module = importlib.reload(app.main)
        assert "app" not in vars(module)
    finally:
        # Reload again with the real get_settings, so later tests don't inherit the stub.
        monkeypatch.undo()
        importlib.reload(app.main)


def test_app_attribute_is_built_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def test_settings() -> Settings:
        calls.append(1)
        return Settings(_env_file=None, app_env="test")

    monkeypatch.setattr(app.main, "get_settings", test_settings)
    app.main._default_app.cache_clear()
    try:
        first = app.main.app
        assert isinstance(first, FastAPI)
        assert app.main.app is first
        assert calls == [1]
    finally:
        app.main._default_app.cache_clear()


def test_unknown_attribute_raises() -> None:
    with pytest.raises(AttributeError):
        _ = app.main.not_a_thing  # type: ignore[attr-defined]
