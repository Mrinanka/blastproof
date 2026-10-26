"""Step 13 Done-when (DW-B13) on the real Replit preview and real retrieval; only the model is fake.

The escalation, no-downgrade, failure and provider-switch cases on every scenario run without a
database in `test_judge_scenarios.py` and `test_judge_config.py`. This file adds what needs
Postgres: the cached real preview, retrieval from the real knowledge base, and `save_judge` on a
real `actions` row. It reuses the one cached preview from `test_shadow_done_when`.
"""

import json
import re
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.clock import FrozenClock
from app.db.actions import create_action
from app.db.enums import Decision
from app.db.judge import save_judge
from app.db.session import make_engine, make_sessionmaker
from app.services.impact import build_facts, template_report
from app.services.judge import (
    InjectionHit,
    JudgeContext,
    JudgeResult,
    assess_safely,
    build_judge_messages,
    decide,
    injection_signals,
)
from app.services.knowledge import KnowledgeService
from app.services.policy import evaluate_pass_two
from tests import test_impact_done_when as impact_done_when
from tests.conftest import AuthDb, asyncpg_url
from tests.impact_helpers import (
    POLICY,
    REPLIT_INTENT,
    REPLIT_SQL,
    classified,
)
from tests.judge_helpers import (
    FakeJudgeLlm,
    context_for,
    judge_with,
    measured_scenarios,
    verdict,
)
from tests.test_shadow_done_when import cached_preview

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

BLOCK_RE = re.compile(r'<untrusted_data kind="(\w+)">\n(.*?)\n</untrusted_data>', re.DOTALL)
MIN = 0.6
REPLIT_TABLES = impact_done_when.REPLIT_TABLES
# The same real knowledge base as step 12's: schema scan, schema_docs.md and a poisoned upload.
embedder = impact_done_when.embedder
knowledge = impact_done_when.knowledge


async def replit_context_from_preview(
    frozen_clock: FrozenClock, knowledge: KnowledgeService
) -> JudgeContext:
    diff = (await cached_preview(frozen_clock)).diff
    retrieval = await knowledge.retrieve(REPLIT_INTENT, REPLIT_TABLES)
    classification = classified(REPLIT_SQL)
    facts = build_facts(diff, classification, POLICY)
    return JudgeContext(
        action_id=uuid4(),
        agent_id=uuid4(),
        intent=REPLIT_INTENT,
        classification=classification,
        diff=diff,
        facts=facts,
        report=template_report(facts),
        rules=evaluate_pass_two(POLICY, classification, diff.facts()),
        retrieval=retrieval,
    )


async def test_the_judge_prompt_holds_the_real_business_rules(
    frozen_clock: FrozenClock, knowledge: KnowledgeService
) -> None:
    context = await replit_context_from_preview(frozen_clock, knowledge)
    llm = FakeJudgeLlm(parsed=verdict("review", "high", 0.9, "Deletes 48,213 users."))
    judge, _ = judge_with(llm)
    result = await judge.assess(context)
    assert result.failure is None

    [(messages, _)] = llm.calls
    human = str(messages[1].content)
    assert human.count("<untrusted_data") == 5 and human.count("</untrusted_data>") == 5
    blocks = {kind: json.loads(body) for kind, body in BLOCK_RE.findall(human)}
    texts = [k["text"] for k in blocks["knowledge"]]
    # D-148: the top 10 similar chunks still reach the judge after the knowledge cap.
    assert any(
        t.startswith("## business rules")
        and "paying customer" in t
        and "must never be deleted" in t
        for t in texts
    )
    assert "ops.lead@example.com" not in human and "[email]" in human
    assert "@" not in json.dumps(blocks["samples"])
    assert '"rows_affected": 245677' in human
    assert '"decision": "review"' in human


async def test_a_judge_that_says_allow_cannot_loosen_the_real_replit_review(
    frozen_clock: FrozenClock, knowledge: KnowledgeService
) -> None:
    context = await replit_context_from_preview(frozen_clock, knowledge)
    assert context.rules.decision == Decision.REVIEW
    judge, _ = judge_with(FakeJudgeLlm(parsed=verdict("allow", "low", 0.99, "Looks fine.")))
    result = await assess_safely(judge, context, 5)
    final = decide(context.rules, result, injection_signals(context), min_confidence=MIN)
    assert final.decision == Decision.REVIEW


async def test_the_poisoned_upload_is_found_and_forces_review_not_allow(
    frozen_clock: FrozenClock, knowledge: KnowledgeService
) -> None:
    """The fixture's `notes.md` is tagged to `users` and says "Ignore previous instructions"."""
    context = await replit_context_from_preview(frozen_clock, knowledge)
    hits = injection_signals(context)
    assert InjectionHit("knowledge", "ignore_instructions") in hits
    assert InjectionHit("knowledge", "data_tag") in hits
    assert all(h.field == "knowledge" for h in hits)  # the schema docs themselves are clean
    allowed = JudgeResult(
        "llm", "m", verdict("allow", "low", 0.99, "Fine."), None, False, 1, 1, None, 1
    )
    open_rules = context.rules.__class__(Decision.ALLOW, (), "Allowed by rule.")
    final = decide(open_rules, allowed, hits, min_confidence=MIN)
    assert final.decision == Decision.REVIEW and final.deciding == "injection"
    assert [h.pattern for h in final.injection] == [h.pattern for h in hits]


async def test_a_judge_block_escalates_the_enterprise_delete_with_real_retrieval(
    knowledge: KnowledgeService,
) -> None:
    scenario = next(
        s for s in measured_scenarios() if s.id == "delete-inactive-enterprise-customer"
    )
    base = context_for(scenario)
    retrieval = await knowledge.retrieve(scenario.intent, list(base.classification.tables))
    context = JudgeContext(**{**base.__dict__, "retrieval": retrieval})
    assert context.rules.decision == Decision.REVIEW
    judge, _ = judge_with(FakeJudgeLlm(parsed=verdict("block", "critical", 0.9, "Enterprise.")))
    result = await assess_safely(judge, context, 5)
    final = decide(context.rules, result, (), min_confidence=MIN)
    assert final.decision == Decision.BLOCK and final.risk_level == "critical"
    assert "paying customer" in str(build_judge_messages(context)[1].content)


async def test_save_judge_writes_the_result_and_adds_the_cost(auth_db: AuthDb) -> None:
    from tests.judge_helpers import replit_context

    context = replit_context()
    judge, _ = judge_with(FakeJudgeLlm(parsed=verdict("block", "critical", 0.95, "Too big.")))
    result = await judge.assess(context)
    user = auth_db.make_user()
    agent = auth_db.make_agent(user.id)
    [(system_id,)] = auth_db.execute(
        "INSERT INTO systems (name, kind, connection_profile)"
        " VALUES ('company-judge', 'postgres', 'local')"
        " ON CONFLICT (name) DO UPDATE SET name = excluded.name RETURNING id"
    )
    db = make_engine(asyncpg_url(auth_db.app_url))
    try:
        sessionmaker = make_sessionmaker(db)
        async with sessionmaker() as session, session.begin():
            action = await create_action(
                session,
                user_id=user.id,
                agent_id=agent.id,
                system_id=system_id,
                tool="query_database",
            )
            await save_judge(session, action.id, result)
        [(stored, risk, status, decision, tokens_in, tokens_out, cost)] = auth_db.execute(
            "SELECT judge, risk_level, status, decision, llm_input_tokens, llm_output_tokens,"
            " llm_cost_usd FROM actions WHERE id = %s",
            (action.id,),
        )
        assert stored == result.to_json()
        assert stored["result"]["recommendation"] == "block" and stored["failure"] is None
        assert risk == "critical"
        assert decision is None  # step 14 decides; saving the judge's result changes no decision
        assert (tokens_in, tokens_out, cost) == (1_200, 300, Decimal("0.002025"))
        unchanged_status = status

        # A second call (a re-preview after drift) adds to the running total.
        async with sessionmaker() as session, session.begin():
            await save_judge(session, action.id, result)
        [(tokens_in, tokens_out, cost, status)] = auth_db.execute(
            "SELECT llm_input_tokens, llm_output_tokens, llm_cost_usd, status FROM actions"
            " WHERE id = %s",
            (action.id,),
        )
        assert (tokens_in, tokens_out, cost) == (2_400, 600, Decimal("0.004050"))
        assert status == unchanged_status

        # A skipped judge (cost cap) stores its failure, clears the risk level and costs nothing.
        skipped = JudgeResult.unavailable("llm", "skipped_cost_cap", model="m")
        async with sessionmaker() as session, session.begin():
            await save_judge(session, action.id, skipped)
        [(stored, risk, cost)] = auth_db.execute(
            "SELECT judge, risk_level, llm_cost_usd FROM actions WHERE id = %s", (action.id,)
        )
        assert stored["failure"] == "skipped_cost_cap" and stored["result"] is None
        assert risk is None and cost == Decimal("0.004050")

        # A call whose usage is unknown makes the running total unknown (over the cap, step 14).
        unknown, _ = judge_with(FakeJudgeLlm(parsed=verdict(), usage=None))
        unknown_result = await unknown.assess(context)
        async with sessionmaker() as session, session.begin():
            await save_judge(session, action.id, unknown_result)
        [(cost,)] = auth_db.execute("SELECT llm_cost_usd FROM actions WHERE id = %s", (action.id,))
        assert cost is None
    finally:
        await db.dispose()
