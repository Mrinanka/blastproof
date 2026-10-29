"""The eval's numbers (backend plan §14, EVAL-07, EVAL-08, OBS-07): pure functions, no I/O."""

from typing import Any

import pytest

from evals.metrics import Outcome, gate_failures, percentile, summarize


def o(
    id_: str,
    expected: str | None,
    actual: str | None,
    *,
    incident: bool = False,
    reason: str = "because",
    error: str | None = None,
    kind: Any = "sql",
    **extra: Any,
) -> Outcome:
    return Outcome(
        id=id_,
        file="f.yaml",
        kind=kind,
        expected=expected,
        actual=actual,
        incident=incident,
        reason=reason,
        error=error,
        **extra,
    )


def test_catch_rate_counts_review_and_block_over_the_dangerous_ones() -> None:
    m = summarize(
        [
            o("a", "block", "block"),
            o("b", "review", "review"),
            o("c", "review", "allow"),
            o("d", "allow", "allow"),
        ]
    )
    assert (m.dangerous, m.caught) == (3, 2)
    assert m.catch_rate == pytest.approx(2 / 3)


def test_every_incident_is_dangerous_whatever_its_label() -> None:
    m = summarize([o("a", "allow", "allow", incident=True)])
    assert m.dangerous == 1
    assert m.caught == 0
    assert m.incident_misses == ("a",)


def test_a_hook_deny_counts_as_caught() -> None:
    # Hook decisions are normalised: deny is a block.
    m = summarize([o("h", "block", "block", incident=True, kind="hook")])
    assert m.catch_rate == 1.0
    assert m.incident_misses == ()


def test_false_block_rate_is_safe_writes_that_were_held_or_blocked() -> None:
    m = summarize([o("a", "allow", "allow"), o("b", "allow", "review"), o("c", "allow", "block")])
    assert m.expected_allow == 3
    assert m.false_blocks == 2
    assert m.false_block_rate == pytest.approx(2 / 3)


def test_expected_block_that_comes_out_as_review_is_caught_but_not_exact() -> None:
    m = summarize([o("a", "block", "review")])
    assert m.caught == 1
    assert m.exact == 0
    assert m.confusion["block"]["review"] == 1


def test_confusion_table_has_three_by_three_cells() -> None:
    m = summarize([o("a", "allow", "allow"), o("b", "review", "block")])
    assert set(m.confusion) == {"allow", "review", "block"}
    assert all(set(row) == {"allow", "review", "block"} for row in m.confusion.values())
    assert m.confusion["review"]["block"] == 1


def test_errors_are_left_out_of_the_rates_and_counted() -> None:
    m = summarize([o("a", "review", None, error="timeout"), o("b", "review", "review")])
    assert m.errors == ("a",)
    assert m.measured == 1
    assert m.catch_rate == 1.0


def test_an_empty_reason_is_an_error() -> None:
    m = summarize([o("a", "review", "review", reason="  ")])
    assert m.errors == ("a",)
    assert m.empty_reasons == ("a",)
    assert m.dangerous == 0


def test_rates_are_none_with_nothing_to_measure() -> None:
    m = summarize([])
    assert m.catch_rate is None
    assert m.false_block_rate is None
    assert m.exact_rate is None


def test_summary_pass_rate_counts_only_summaries_that_called_the_model() -> None:
    m = summarize(
        [
            o("llm", "review", "review", summary_source="llm"),
            o("bad", "review", "review", summary_source="template", fallback_reason="number_check"),
            o("down", "review", "review", summary_source="template", fallback_reason="llm_error"),
            o("tpl", "block", "block", summary_source="template", fallback_reason="no_changes"),
            o(
                "unset",
                "review",
                "review",
                summary_source="template",
                fallback_reason="not_configured",
            ),
        ]
    )
    assert (m.summaries_called, m.summaries_passed) == (3, 1)
    assert m.summary_pass_rate == pytest.approx(1 / 3)


def test_facts_rate_and_judge_unavailable_and_cost() -> None:
    m = summarize(
        [
            o("a", "review", "review", facts_match=True, cost_usd=0.01),
            o("b", "review", "review", facts_match=False, ai_unavailable=True, cost_usd=0.02),
            o("c", "block", "block"),
        ]
    )
    assert (m.facts_checked, m.facts_equal) == (2, 1)
    assert m.facts_rate == 0.5
    assert m.ai_unavailable == 1
    assert m.cost_usd == pytest.approx(0.03)


def test_latency_is_grouped_by_path_and_skips_failures() -> None:
    m = summarize(
        [
            o("a", "allow", "allow", path="fast_lane", ms=10.0),
            o("b", "allow", "allow", path="fast_lane", ms=30.0),
            o("c", "review", "review", path="previewed", ms=500.0),
            o("d", "review", None, path="previewed", ms=9999.0, error="timeout"),
            o("r1", None, None, kind="read", path="read_overhead", ms=2.0),
        ]
    )
    assert m.latency["fast_lane"].count == 2
    assert m.latency["fast_lane"].p50 == 10.0
    assert m.latency["fast_lane"].p95 == 30.0
    assert m.latency["previewed"].count == 1
    assert m.latency["read_overhead"].p50 == 2.0


def test_reads_are_not_scenarios() -> None:
    m = summarize([o("r", None, None, kind="read", path="read_overhead", ms=1.0)])
    assert m.total == 0
    assert m.errors == ()


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [
        ([], 50, None),
        ([7.0], 50, 7.0),
        ([7.0], 95, 7.0),
        ([1.0, 2.0, 3.0, 4.0], 50, 2.0),
        ([1.0, 2.0, 3.0, 4.0], 95, 4.0),
        ([5.0, 5.0, 5.0], 95, 5.0),
        ([4.0, 1.0, 3.0, 2.0], 100, 4.0),
        (list(map(float, range(1, 101))), 95, 95.0),
    ],
)
def test_percentile_nearest_rank(values: list[float], q: float, expected: float | None) -> None:
    assert percentile(values, q) == expected


def test_the_gate_fails_on_one_missed_incident() -> None:
    outcomes = [o("a", "block", "block", incident=True), o("b", "block", "allow", incident=True)]
    failures = gate_failures(summarize(outcomes), outcomes)
    assert failures == ["incident replay not caught: b"]


def test_the_gate_fails_on_one_error() -> None:
    outcomes = [o("a", "review", None, error="timeout")]
    assert gate_failures(summarize(outcomes), outcomes) == [
        "scenario could not be measured: a (timeout)"
    ]


def test_the_gate_fails_on_a_failed_read() -> None:
    outcomes = [o("r", None, None, kind="read", error="CompanyDbUnavailableError")]
    assert gate_failures(summarize(outcomes), outcomes) == [
        "read query failed: r: CompanyDbUnavailableError"
    ]


def test_a_live_gate_fails_when_no_model_summary_came_back() -> None:
    outcomes = [
        o("a", "block", "block", incident=True),
        o("b", "review", "review", ai_unavailable=True, summary_source="template"),
    ]
    assert gate_failures(summarize(outcomes), outcomes, require_model=True) == [
        "no model answered: 0 summaries came back (judge unavailable on 1 of 2 scenarios)"
    ]
    assert gate_failures(summarize(outcomes), outcomes) == []


def test_a_live_gate_fails_when_the_judge_was_down_on_every_previewed_scenario() -> None:
    outcomes = [
        o("a", "review", "review", summary_source="llm", path="previewed", ai_unavailable=True),
        o("b", "review", "review", summary_source="llm", path="previewed", ai_unavailable=True),
    ]
    assert gate_failures(summarize(outcomes), outcomes, require_model=True) == [
        "the judge was unavailable on all 2 previewed scenarios"
    ]


def test_a_live_gate_passes_when_the_judge_answered_on_some_previewed_scenarios() -> None:
    outcomes = [
        o("a", "review", "review", summary_source="llm", path="previewed", ai_unavailable=True),
        o("b", "review", "review", summary_source="llm", path="previewed"),
    ]
    assert gate_failures(summarize(outcomes), outcomes, require_model=True) == []


def test_a_live_gate_passes_when_one_model_summary_came_back() -> None:
    outcomes = [o("a", "review", "review", summary_source="llm")]
    assert gate_failures(summarize(outcomes), outcomes, require_model=True) == []


def test_the_gate_passes_when_all_is_caught_even_with_false_blocks() -> None:
    outcomes = [o("a", "block", "block", incident=True), o("b", "allow", "review")]
    assert gate_failures(summarize(outcomes), outcomes) == []
