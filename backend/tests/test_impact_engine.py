"""Step 12 (BE-65, BE-68, BE-69, OBS-05): the engine calls the model once, checks what comes back,
and falls back to the template on any problem without raising."""

import asyncio
import json
from decimal import Decimal
from uuid import UUID

import pytest

from app.logging import configure_logging
from app.services.impact import ImpactEngine, ImpactReport, ImpactResult
from app.services.knowledge import Retrieval, RetrievedChunk
from app.services.llm import LlmConfigError
from app.services.shadow import Diff, TableDiff
from tests.impact_helpers import (
    DONE_WHEN_HEADLINE,
    REPLIT_INTENT,
    REPLIT_SQL,
    FakeLlm,
    classified,
    engine_with,
    replit_diff,
    report,
)

ACTION_ID = UUID("00000000-0000-4000-8000-000000000012")
AGENT_ID = UUID("00000000-0000-4000-8000-0000000000a6")
GOOD = report(DONE_WHEN_HEADLINE, ["241,295 rows are deleted."], ["12 paying customers."])
# 1,200 x $0.75 + 300 x $3.75, per million tokens
GOOD_COST = Decimal("0.002025")


async def summarize(
    engine: ImpactEngine,
    *,
    diff: Diff | None = None,
    retrieval: Retrieval | None = None,
    sql: str = REPLIT_SQL,
) -> ImpactResult:
    return await engine.summarize(
        action_id=ACTION_ID,
        agent_id=AGENT_ID,
        intent=REPLIT_INTENT,
        classification=classified(sql),
        diff=diff if diff is not None else replit_diff(),
        retrieval=retrieval,
    )


async def test_a_good_report_is_used_with_its_tokens_and_cost() -> None:
    llm = FakeLlm(parsed=GOOD)
    engine, factory = engine_with(llm)
    result = await summarize(engine)
    assert result.source == "llm" and result.fallback_reason is None
    assert result.report == GOOD
    assert (result.input_tokens, result.output_tokens) == (1_200, 300)
    assert result.cost_usd == GOOD_COST
    assert result.facts.total_rows_affected == 245_677
    assert result.report_json() == {
        "headline": DONE_WHEN_HEADLINE,
        "key_points": ["241,295 rows are deleted."],
        "concerns": ["12 paying customers."],
        "source": "llm",
        "fallback_reason": None,
    }
    assert factory.builds == 1


async def test_the_structured_runnable_is_asked_for_the_raw_message() -> None:
    engine, factory = engine_with(FakeLlm(parsed=GOOD))
    await summarize(engine)
    assert factory.models[0].structured_calls == [(ImpactReport, True)]


async def test_a_dict_from_the_model_is_accepted() -> None:
    engine, _ = engine_with(FakeLlm(parsed=GOOD.model_dump()))
    result = await summarize(engine)
    assert result.source == "llm" and result.report == GOOD


async def test_personal_data_in_the_report_is_masked() -> None:
    llm = FakeLlm(parsed=report("Deletes 48,213 users such as bob@example.com."))
    engine, _ = engine_with(llm)
    result = await summarize(engine)
    assert result.source == "llm"
    assert result.report.headline == "Deletes 48,213 users such as [email]."


def assert_template(result: ImpactResult, reason: str) -> None:
    assert result.source == "template"
    assert result.fallback_reason == reason
    assert result.report.headline.startswith("Deletes 48,213 rows from users")


async def test_a_raising_model_gives_the_template() -> None:
    engine, _ = engine_with(FakeLlm(raises=RuntimeError("quota for key AIza-secret")))
    result = await summarize(engine)
    assert_template(result, "llm_error")
    assert result.input_tokens is None and result.cost_usd is None


async def test_a_hanging_model_times_out_to_the_template() -> None:
    engine, _ = engine_with(FakeLlm(hang=True), timeout_seconds=0.05)
    result = await summarize(engine)
    assert_template(result, "llm_timeout")
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (None, None, None)
    assert result.duration_ms < 2_000


def test_the_default_timeout_is_llm_timeout_seconds() -> None:
    engine, _ = engine_with(FakeLlm(), llm_timeout_seconds=7)
    assert engine._timeout == 7


@pytest.mark.parametrize(
    "llm",
    [
        FakeLlm(parsed=None, parsing_error=ValueError("bad json")),
        FakeLlm(parsed=GOOD, parsing_error=ValueError("partly bad")),
        FakeLlm(parsed=None),
        FakeLlm(parsed={"headline": "Only a headline"}),
        FakeLlm(parsed={"headline": 3, "key_points": [], "concerns": []}),
    ],
    ids=["error", "error-with-parsed", "parsed-none", "missing-fields", "wrong-type"],
)
async def test_an_unparseable_result_gives_the_template_and_keeps_tokens(llm: FakeLlm) -> None:
    engine, _ = engine_with(llm)
    result = await summarize(engine)
    assert_template(result, "parse_error")
    assert (result.input_tokens, result.output_tokens) == (1_200, 300)
    assert result.cost_usd == GOOD_COST


async def test_a_result_that_is_not_a_mapping_is_a_parse_error() -> None:
    engine, _ = engine_with(FakeLlm(output=["not", "a", "dict"]))
    result = await summarize(engine)
    assert_template(result, "parse_error")
    assert result.cost_usd is None  # no raw message, so the usage is unknown


@pytest.mark.parametrize(
    "bad",
    [
        report("   "),
        report(""),
        report("x" * 201),
        report("Deletes 48,213 users.", key_points=["Fine."] * 9),
        report("Deletes 48,213 users.", concerns=["Fine."] * 9),
        report("Deletes 48,213 users.", key_points=["y" * 301]),
        report("Deletes 48,213 users.", concerns=["Fine.", " "]),
        # The live eval's reply that passed .strip() and then failed the JSONB write.
        report("\N{NO-BREAK SPACE}\N{ZERO WIDTH SPACE}" * 3 + "\x00"),
        report("Deletes 48,213 users.\x00"),
        report("Deletes 48,213 users.", key_points=["Fine.\x00"]),
        report("\N{ZERO WIDTH SPACE}\N{ZERO WIDTH SPACE}"),
        report("Deletes 48,213 users.", concerns=["\ud800"]),
    ],
    ids=[
        "blank",
        "empty",
        "long-headline",
        "9-points",
        "9-concerns",
        "long-item",
        "blank-item",
        "ci-junk",
        "nul-headline",
        "nul-item",
        "zero-width-only",
        "lone-surrogate",
    ],
)
async def test_a_badly_shaped_report_gives_the_template(bad: ImpactReport) -> None:
    engine, _ = engine_with(FakeLlm(parsed=bad))
    result = await summarize(engine)
    assert_template(result, "bad_shape")
    assert result.cost_usd == GOOD_COST


async def test_the_limits_themselves_are_accepted() -> None:
    edge = report("x" * 200, key_points=["y" * 300] * 8, concerns=["z" * 300] * 8)
    engine, _ = engine_with(FakeLlm(parsed=edge))
    assert (await summarize(engine)).source == "llm"


async def test_an_invented_number_gives_the_template_with_tokens() -> None:
    bad = report(DONE_WHEN_HEADLINE.replace("12 paying", "13 paying"))
    engine, _ = engine_with(FakeLlm(parsed=bad))
    result = await summarize(engine)
    assert_template(result, "number_check")
    assert (result.input_tokens, result.output_tokens) == (1_200, 300)
    assert result.cost_usd == GOOD_COST


async def test_a_number_from_knowledge_is_refused() -> None:
    rules = RetrievedChunk(
        chunk_id="c1",
        source="schema_docs.md",
        table_names=[],
        content="Enterprise accounts need 45 days of notice before deletion.",
        match="similar",
        score=0.8,
    )
    knowledge = Retrieval(chunks=[rules], refs=[], duration_ms=1)
    llm = FakeLlm(parsed=report("Deletes 48,213 users.", concerns=["They need 45 days notice."]))
    engine, _ = engine_with(llm)
    result = await summarize(engine, retrieval=knowledge)
    assert_template(result, "number_check")


async def test_a_model_that_cannot_be_built_gives_the_template_then_retries() -> None:
    llm = FakeLlm(parsed=GOOD)
    engine, factory = engine_with(llm, failures=1)
    first = await summarize(engine)
    assert_template(first, "not_configured")
    assert (first.input_tokens, first.output_tokens, first.cost_usd) == (0, 0, Decimal(0))
    assert llm.calls == []
    second = await summarize(engine)
    assert second.source == "llm"
    await summarize(engine)
    assert factory.builds == 2  # retried once after the failure, then cached


async def test_no_changes_makes_no_call() -> None:
    llm = FakeLlm(parsed=GOOD)
    engine, factory = engine_with(llm)
    empty = Diff(tables={"users": TableDiff("users", rows_before=50_000)})
    result = await summarize(engine, diff=empty)
    assert result.source == "template" and result.fallback_reason == "no_changes"
    assert result.report == report("Changes no rows.")
    assert result.cost_usd == Decimal(0)
    assert llm.calls == [] and factory.builds == 0


async def test_missing_usage_leaves_tokens_and_cost_unknown() -> None:
    engine, _ = engine_with(FakeLlm(parsed=GOOD, usage=None))
    result = await summarize(engine)
    assert result.source == "llm"
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (None, None, None)


async def test_the_run_config_names_the_action_agent_and_env() -> None:
    llm = FakeLlm(parsed=GOOD)
    engine, _ = engine_with(llm)
    await summarize(engine)
    [(messages, config)] = llm.calls
    assert config == {
        "run_name": "impact_summary",
        "metadata": {"action_id": str(ACTION_ID), "agent_id": str(AGENT_ID), "env": "test"},
        "tags": ["impact"],
    }
    assert [m.type for m in messages] == ["system", "human"]


def test_the_template_alone_makes_no_call() -> None:
    llm = FakeLlm(parsed=GOOD)
    engine, factory = engine_with(llm)
    result = engine.template(classification=classified(REPLIT_SQL), diff=replit_diff())
    assert result.source == "template" and result.fallback_reason is None
    assert result.cost_usd == Decimal(0)
    assert factory.builds == 0 and llm.calls == []


def test_a_model_without_a_price_stops_the_engine() -> None:
    with pytest.raises(LlmConfigError, match="LLM_PRICES has no entry"):
        engine_with(FakeLlm(), summary_model="anthropic:claude-haiku-5-5")


async def test_concurrent_summaries_share_one_built_model() -> None:
    llm = FakeLlm(parsed=GOOD)
    engine, factory = engine_with(llm)
    results = await asyncio.gather(*(summarize(engine) for _ in range(5)))
    assert {r.source for r in results} == {"llm"}
    assert factory.builds == 1 and len(llm.calls) == 5


SECRET_SQL = "DELETE FROM users WHERE email = 'leak.me@example.com'"


@pytest.mark.parametrize(
    "llm",
    [
        FakeLlm(parsed=report("Deletes 48,213 users: SECRET-REPORT-TEXT.")),
        FakeLlm(parsed=report("Deletes 99,999 users: SECRET-REPORT-TEXT.")),
        FakeLlm(raises=RuntimeError("SECRET-EXCEPTION-TEXT leak.me@example.com")),
    ],
    ids=["success", "number-check", "error"],
)
async def test_logs_hold_no_sql_report_prompt_or_exception_text(
    llm: FakeLlm, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    engine, _ = engine_with(llm)
    await summarize(engine, sql=SECRET_SQL)
    out = capsys.readouterr()
    logged = out.out + out.err
    assert "impact_done" in logged  # the log is being captured at all
    for line in logged.splitlines():
        if line.startswith("{"):
            record = json.loads(line)
            if record.get("event") == "impact_done":
                assert record["action_id"] == str(ACTION_ID)
                assert {"source", "fallback_reason", "duration_ms", "cost_usd"} <= set(record)
    for secret in (
        "leak.me",
        "SECRET-REPORT-TEXT",
        "SECRET-EXCEPTION-TEXT",
        "DELETE FROM",
        "Clean up inactive users",
        "untrusted_data",
    ):
        assert secret not in logged


async def test_a_build_failure_logs_only_the_class(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    engine, _ = engine_with(FakeLlm(parsed=GOOD), failures=1)
    await summarize(engine)
    logged = capsys.readouterr().out
    assert "llm_build_failed" in logged and "RuntimeError" in logged
    assert "secret-option-value" not in logged
