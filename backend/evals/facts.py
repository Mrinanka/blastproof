"""Facts the eval scenarios are judged on without a shadow run (backend plan §14).

`SEED_ROWS` is the row count per table of the seeded company_main; `diff_from` builds the `Diff` the
shadow run would produce from a scenario's `expected_impact` (proved against the seed by
`tests/test_seed_counts.py`).
"""

from collections.abc import Mapping, Sequence

from app.services.policy import DiffFacts, TableFacts
from app.services.shadow import CascadePath, Diff, SampleRow, TableDiff
from evals.schema import ExpectedImpact

# Rows per table in the seeded company_main (counted on the local seed, 2026-10-26).
SEED_ROWS: Mapping[str, int] = {
    "customer_notes": 6_303,
    "feature_flags": 25,
    "invoices": 10_087,
    "order_items": 128_648,
    "orders": 51_411,
    "organizations": 2_000,
    "payments": 10_087,
    "products": 40,
    "subscriptions": 2_107,
    "support_tickets": 12_062,
    "users": 50_000,
}


def diff_from(
    impact: ExpectedImpact,
    targets: Sequence[str],
    *,
    cascades: Sequence[CascadePath] = (),
    samples: Sequence[SampleRow] = (),
    rows: Mapping[str, int] = SEED_ROWS,
) -> Diff:
    """A `Diff` as the shadow run would build it from a scenario's measured impact."""
    tables = {name: TableDiff(name, rows_before=n) for name, n in rows.items()}
    for name, change in impact.table_changes.items():
        tables[name] = TableDiff(
            name,
            deleted=change.deleted,
            updated=change.updated,
            inserted=change.inserted,
            rows_before=rows[name],
            is_cascade=name not in targets,
        )
    return Diff(
        tables=tables,
        breakdowns={k: dict(v) for k, v in impact.breakdowns.items()},
        derived_groups=dict(impact.derived_groups),
        cascade_expectations=tuple(cascades),
        samples=tuple(samples),
    )


def facts_from(
    impact: ExpectedImpact, targets: Sequence[str], *, rows: Mapping[str, int] = SEED_ROWS
) -> DiffFacts:
    """The policy's `DiffFacts` for a scenario's measured impact (pass 2 without a shadow run)."""
    tables = {
        table: TableFacts(
            deleted=change.deleted,
            updated=change.updated,
            inserted=change.inserted,
            rows_before=rows[table],
            is_cascade=table not in targets,
        )
        for table, change in impact.table_changes.items()
    }
    return DiffFacts(tables=tables, derived_groups=dict(impact.derived_groups))
