"""SEC-15: every LangSmith trace leaves through a client that anonymizes it first."""

import json
import time
from typing import Any

import pytest
import requests
from langchain_core.tracers.langchain import get_client
from langsmith import utils as ls_utils

from app import tracing
from app.config import Settings
from app.services.llm import build_chat_model
from app.services.redaction import set_known_secrets

DSN = "postgresql://app:hunter2hunter2@ep-x.neon.tech/db"
PLANTED = {
    "email": "ana@example.com",
    "phone": "+1 (415) 555-0132",
    "dsn_password": "hunter2hunter2",
    "key": "known-secret-value-1234",
}


class RecordingSession(requests.Session):
    """Records every request the client would send; nothing leaves the process."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, str, Any]] = []

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        self.sent.append((method, url, kwargs.get("data") or kwargs.get("json")))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response


@pytest.fixture(autouse=True)
def _clean_registry():
    set_known_secrets([])
    yield
    set_known_secrets([])


def _client(session: RecordingSession) -> Any:
    ls_utils.get_env_var.cache_clear()
    return tracing.masked_client(
        api_key="test-key",
        api_url="http://langsmith.invalid",
        session=session,
        auto_batch_tracing=False,
        info={},
    )


def test_masked_client_sends_nothing_in_clear() -> None:
    set_known_secrets([PLANTED["key"]])
    session = RecordingSession()
    client = _client(session)
    run_id = "3f2b8c1e-5a4d-4e7f-9b6a-1c2d3e4f5a6b"
    text = f"{PLANTED['email']} {PLANTED['phone']} {DSN} {PLANTED['key']}"
    client.create_run(
        name="write_pipeline",
        id=run_id,
        run_type="chain",
        inputs={"intent": text},
        extra={"metadata": {"action_id": run_id, "agent_id": "a-1", "env": "test", "note": text}},
        start_time=None,
    )
    client.update_run(
        run_id, outputs={"summary": text}, error=f"RuntimeError: cannot reach {DSN} {text}"
    )
    payloads = [d for _, _, d in session.sent if d]
    assert payloads, "the recording session saw no request"
    raw = " ".join(p.decode() if isinstance(p, bytes) else json.dumps(p) for p in payloads)
    for planted in PLANTED.values():
        assert planted not in raw
    assert run_id in raw  # the metadata that finds a trace survives
    assert '"env"' in raw


def test_configure_tracing_installs_the_masked_client_once() -> None:
    settings = Settings(_env_file=None)
    tracing.configure_tracing(settings)
    first = get_client()
    tracing.configure_tracing(settings)
    assert get_client() is first
    assert first is tracing.installed_client()


def test_configure_tracing_refreshes_the_known_secrets() -> None:
    from pydantic import SecretStr

    from app.services.redaction import redact

    tracing.configure_tracing(Settings(_env_file=None, jwt_secret=SecretStr("k" * 40)))
    assert redact("k" * 40) == "[redacted]"
    tracing.configure_tracing(Settings(_env_file=None, jwt_secret=SecretStr("m" * 40)))
    assert redact("k" * 40) == "k" * 40
    assert redact("m" * 40) == "[redacted]"


def test_the_model_does_not_expose_its_key_to_traces() -> None:
    settings = Settings(_env_file=None)
    model = build_chat_model(
        "google_genai:gemini-3.8-flash", {"api_key": "PLANTEDKEY1234567890"}, settings
    )
    assert "PLANTEDKEY1234567890" not in str(model._get_invocation_params())
    assert "PLANTEDKEY1234567890" not in str(model._get_ls_params())


def test_building_the_client_with_tracing_off_opens_no_connection(
    monkeypatch: pytest.MonkeyPatch, no_network: Any
) -> None:
    # The client's batching thread asks the LangSmith API for `/info` unless it already has it. With
    # tracing off nothing may leave the process (TEST-07: building the app stays offline).
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing.langsmith, "configure", lambda **_: None)
    tracing.configure_tracing(Settings(_env_file=None))
    client = tracing.installed_client()
    assert client is not None
    assert client._info is not None  # known up front, so nothing is fetched
    time.sleep(0.5)  # let the background thread run
    assert no_network.blocked == []
