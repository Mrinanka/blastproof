"""Step 16: rebuilding the target's changes from RETURNING rows, and finding drift."""

import pytest

from app.services.commit_sql import (
    Drift,
    Expected,
    ReturnedDiff,
    ReturnedRow,
    canonical_key,
    cascade_key,
    find_drift,
    returned_diff,
)


def key(n: int) -> str:
    return canonical_key({"id": n})


def row(old: int | None, new: int | None, old_hash: str | None = None, new_hash: str | None = None):
    return ReturnedRow(
        None if old is None else key(old),
        None if new is None else key(new),
        old_hash,
        new_hash,
    )


def test_a_key_is_the_same_whatever_its_spelling() -> None:
    assert canonical_key('{"b": 1, "a": 2}') == canonical_key({"a": 2, "b": 1})


def test_returned_rows_become_deleted_updated_and_inserted() -> None:
    result = returned_diff(
        [row(1, None, "a"), row(2, 2, "b", "c"), row(None, 3, None, "d"), row(None, 4, None, "e")]
    )
    assert result == ReturnedDiff(frozenset({key(1)}), frozenset({key(2)}), 2)
    assert result.rows == 4


def test_an_update_that_changed_nothing_is_not_a_change() -> None:
    assert returned_diff([row(1, 1, "same", "same"), row(2, 2, "x", "y")]).updated == {key(2)}


def test_an_update_that_moves_a_row_to_another_key_is_a_delete_and_an_insert() -> None:
    result = returned_diff([row(1, 9, "a", "b")])
    assert result == ReturnedDiff(frozenset({key(1)}), frozenset(), 1)


def test_nothing_returned_is_no_change() -> None:
    assert returned_diff([]) == ReturnedDiff(frozenset(), frozenset(), 0)


# --- find_drift ----------------------------------------------------------------------------------

PATH = ("invoices", "on_delete=set_null")
OTHER = ("orders", "on_delete=cascade")


def approved(**overrides: object) -> Expected:
    values: dict[str, object] = {
        "deleted": {key(1): "h1", key(2): "h2"},
        "updated": {key(3): "h3"},
        "inserted": 2,
        "cascades": {PATH: 5, OTHER: 7},
    }
    return Expected(**(values | overrides))  # type: ignore[arg-type]


HASHES = {key(1): "h1", key(2): "h2", key(3): "h3"}
CASCADES = {PATH: 5, OTHER: 7}
RETURNED = ReturnedDiff(frozenset({key(1), key(2)}), frozenset({key(3)}), 2)


def test_an_exact_match_is_no_drift() -> None:
    assert find_drift(approved(), HASHES, CASCADES, RETURNED) is None


def test_before_the_statement_only_rows_and_cascades_are_checked() -> None:
    assert find_drift(approved(), HASHES, CASCADES) is None
    assert find_drift(approved(), {**HASHES, key(1): "other"}, CASCADES) == Drift(
        "rows", {"rows_changed": 1}
    )


@pytest.mark.parametrize(
    ("hashes", "counts"),
    [
        ({**HASHES, key(2): "changed"}, {"rows_changed": 1}),
        ({k: v for k, v in HASHES.items() if k != key(3)}, {"rows_missing": 1}),
        ({}, {"rows_missing": 3}),
    ],
)
def test_a_changed_or_missing_locked_row_is_drift(
    hashes: dict[str, str], counts: dict[str, int]
) -> None:
    assert find_drift(approved(), hashes, CASCADES, RETURNED) == Drift("rows", counts)


def test_an_extra_deleted_key_is_drift() -> None:
    returned = ReturnedDiff(RETURNED.deleted | {key(9)}, RETURNED.updated, 2)
    assert find_drift(approved(), HASHES, CASCADES, returned) == Drift("keys", {"deleted_extra": 1})


def test_a_missing_deleted_key_is_drift() -> None:
    returned = ReturnedDiff(frozenset({key(1)}), RETURNED.updated, 2)
    assert find_drift(approved(), HASHES, CASCADES, returned) == Drift(
        "keys", {"deleted_missing": 1}
    )


def test_an_extra_or_missing_updated_key_is_drift() -> None:
    extra = ReturnedDiff(RETURNED.deleted, RETURNED.updated | {key(8)}, 2)
    assert find_drift(approved(), HASHES, CASCADES, extra) == Drift("keys", {"updated_extra": 1})
    missing = ReturnedDiff(RETURNED.deleted, frozenset(), 2)
    assert find_drift(approved(), HASHES, CASCADES, missing) == Drift(
        "keys", {"updated_missing": 1}
    )


@pytest.mark.parametrize("inserted", [1, 3, 0])
def test_an_insert_count_that_is_off_is_drift(inserted: int) -> None:
    returned = ReturnedDiff(RETURNED.deleted, RETURNED.updated, inserted)
    assert find_drift(approved(), HASHES, CASCADES, returned) == Drift(
        "inserts", {"inserted_expected": 2, "inserted_returned": inserted}
    )


def test_a_changed_cascade_count_is_drift() -> None:
    assert find_drift(approved(), HASHES, {**CASCADES, PATH: 4}, RETURNED) == Drift(
        "cascades", {"cascade_paths_changed": 1}
    )


def test_a_new_or_vanished_cascade_path_is_drift() -> None:
    new = {**CASCADES, ("support_tickets", "on_delete=cascade"): 1}
    assert find_drift(approved(), HASHES, new, RETURNED) == Drift(
        "cascades", {"cascade_paths_changed": 1}
    )
    gone = {OTHER: 7}
    assert find_drift(approved(), HASHES, gone, RETURNED) == Drift(
        "cascades", {"cascade_paths_changed": 1}
    )


def test_a_path_that_counts_zero_equals_a_path_that_is_absent() -> None:
    assert find_drift(approved(cascades={OTHER: 7}), HASHES, {OTHER: 7, PATH: 0}, RETURNED) is None


def test_the_first_failing_check_names_the_reason_and_all_counts_are_kept() -> None:
    drift = find_drift(approved(), {**HASHES, key(1): "x"}, {**CASCADES, PATH: 0}, RETURNED)
    assert drift == Drift("rows", {"rows_changed": 1, "cascade_paths_changed": 1})


def test_a_drift_holds_numbers_only_never_a_key() -> None:
    drift = find_drift(approved(), {}, {}, ReturnedDiff(frozenset({key(99)}), frozenset(), 9))
    assert drift is not None
    assert all(isinstance(v, int) for v in drift.counts.values())
    assert '"id"' not in repr(drift) and "h1" not in repr(drift)


def test_the_cascade_key_names_the_far_table_the_route_and_the_action() -> None:
    item = {"target": "users", "table": "order_items", "via": ["orders"], "on_delete": "cascade"}
    assert cascade_key(item) == ("order_items", "orders", "on_delete=cascade")
