"""Test doubles for step 12: a structured model that needs no network, and diffs from the scenarios.

`FakeLlm` stands in for `model.with_structured_output(ImpactReport, include_raw=True)`: it returns
`{raw, parsed, parsing_error}` like the real runnable, or raises, or hangs.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage

from app.config import DEFAULT_POLICY_FILE, Settings
from app.services.classifier import Classification, classify
from app.services.company_schema import ForeignKey
from app.services.impact import ImpactEngine, ImpactReport
from app.services.policy import Policy, load_policy
from app.services.shadow import CascadePath, Diff

# Re-exported: the tests import them from here (they live in evals/facts.py for the eval runner).
from evals.facts import SEED_ROWS as SEED_ROWS
from evals.facts import diff_from as diff_from
from evals.schema import SqlScenario, load_scenario_file
from tests.conftest import REPLIT_SCENARIO_ID, SCENARIOS_DIR

POLICY: Policy = load_policy(DEFAULT_POLICY_FILE)

REPLIT_SQL = "DELETE FROM users WHERE last_login_at < now() - interval '6 months'"
REPLIT_INTENT = "Clean up inactive users"
DONE_WHEN_HEADLINE = (
    "Deletes 48,213 users, including 12 paying customers (3 enterprise), and their orders"
    " through a cascade."
)
DEFAULT_USAGE = {"input_tokens": 1_200, "output_tokens": 300, "total_tokens": 1_500}


def fk(child: str, parent: str, on_delete: str = "cascade") -> ForeignKey:
    column = f"{parent.removesuffix('s')}_id"
    return ForeignKey(child, (column,), parent, ("id",), on_delete)


# The Replit delete's foreign-key paths, as step 10 measured them.
REPLIT_CASCADES = (
    CascadePath("users", (fk("customer_notes", "users"),), "cascade", 5_713),
    CascadePath("users", (fk("invoices", "users", "set_null"),), "set_null", 4_382),
    CascadePath("users", (fk("orders", "users"),), "cascade", 48_500),
    CascadePath("users", (fk("orders", "users"), fk("order_items", "orders")), "cascade", 121_364),
    CascadePath("users", (fk("payments", "users"),), "cascade", 4_382),
    CascadePath("users", (fk("subscriptions", "users"),), "cascade", 1_476),
    CascadePath("users", (fk("support_tickets", "users"),), "cascade", 11_647),
)


def scenarios_with_impact() -> list[SqlScenario]:
    found = []
    for name in ("incidents.yaml", "labeled.yaml"):
        for s in load_scenario_file(SCENARIOS_DIR / name).scenarios:
            if isinstance(s, SqlScenario) and s.expected_impact is not None:
                found.append(s)
    return found


def replit_scenario() -> SqlScenario:
    return next(s for s in scenarios_with_impact() if s.id == REPLIT_SCENARIO_ID)


def classified(sql: str) -> Classification:
    from datetime import UTC, datetime

    return classify(sql, now=datetime(2026, 10, 1, tzinfo=UTC))


def replit_diff(**kwargs: Any) -> Diff:
    kwargs.setdefault("cascades", REPLIT_CASCADES)
    return diff_from(replit_scenario().expected_impact, ["users"], **kwargs)  # type: ignore[arg-type]


@dataclass
class FakeLlm:
    """The structured runnable. Set one behaviour; `calls` records (messages, config)."""

    parsed: ImpactReport | dict[str, Any] | None = None
    usage: dict[str, int] | None = field(default_factory=lambda: dict(DEFAULT_USAGE))
    raises: BaseException | None = None
    hang: bool = False
    parsing_error: BaseException | None = None
    output: Any = None  # returned as is when set (a malformed result)
    calls: list[tuple[Any, Any]] = field(default_factory=list)

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        self.calls.append((messages, config))
        if self.hang:
            await asyncio.sleep(3600)
        if self.raises is not None:
            raise self.raises
        if self.output is not None:
            return self.output
        raw = AIMessage(content="{}", usage_metadata=self.usage) if self.usage else AIMessage("{}")
        return {"raw": raw, "parsed": self.parsed, "parsing_error": self.parsing_error}


class FakeChatModel:
    def __init__(self, llm: FakeLlm) -> None:
        self.llm = llm
        self.structured_calls: list[tuple[Any, bool]] = []

    def with_structured_output(self, schema: Any, *, include_raw: bool = False) -> FakeLlm:
        self.structured_calls.append((schema, include_raw))
        return self.llm


@dataclass
class FakeFactory:
    """Builds a FakeChatModel, failing the first `failures` times like a missing key would."""

    llm: FakeLlm
    failures: int = 0
    builds: int = 0
    options_seen: list[Mapping[str, Any]] = field(default_factory=list)
    models: list[FakeChatModel] = field(default_factory=list)

    def __call__(self, model: str, options: Mapping[str, Any], settings: Settings) -> Any:
        self.builds += 1
        self.options_seen.append(dict(options))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("GOOGLE_API_KEY missing: secret-option-value")
        self.models.append(FakeChatModel(self.llm))
        return self.models[-1]


def report(
    headline: str, key_points: Sequence[str] = (), concerns: Sequence[str] = ()
) -> ImpactReport:
    return ImpactReport(headline=headline, key_points=list(key_points), concerns=list(concerns))


def engine_with(
    llm: FakeLlm, *, failures: int = 0, timeout_seconds: float | None = None, **settings: Any
) -> tuple[ImpactEngine, FakeFactory]:
    factory = FakeFactory(llm, failures=failures)
    values: dict[str, Any] = {"app_env": "test"} | settings
    engine = ImpactEngine(
        Settings(_env_file=None, **values),
        POLICY,
        model_factory=factory,
        timeout_seconds=timeout_seconds,
    )
    return engine, factory
