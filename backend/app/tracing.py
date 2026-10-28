"""LangSmith tracing setup (SEC-15, OBS-01): every trace leaves through an anonymizing client.

The client is installed as the process-wide default, so LangChain and LangGraph runs use it without
being told (`langchain_core.tracers.langchain` falls back to the configured client). It is installed
whether tracing is on or off: `LANGSMITH_TRACING` can be switched on later, and a trace must never
go out through an unmasked default client. Inputs, outputs, metadata and error strings pass through
`redact` before upload; metadata such as `action_id` survives because it is a UUID.
"""

from typing import Any

import langsmith
from langsmith import Client
from langsmith.anonymizer import create_anonymizer

from app.config import Settings
from app.services.redaction import known_secrets_from, redact, set_known_secrets

# `Client` starts a background thread, so it is built once per process.
_client: Client | None = None


def masked_client(**client_kwargs: Any) -> Client:
    """A LangSmith client that redacts every string in a run before it is sent."""
    anonymizer = create_anonymizer(lambda value, path: redact(value))
    return Client(anonymizer=anonymizer, **client_kwargs)


def installed_client() -> Client | None:
    return _client


def configure_tracing(settings: Settings) -> None:
    global _client
    set_known_secrets(known_secrets_from(settings))
    if _client is None:
        _client = masked_client()
        langsmith.configure(client=_client)
