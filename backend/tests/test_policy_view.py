"""The rules as the dashboard shows them (BE-114). No Docker."""

from pathlib import Path

from app.config import Settings
from app.services.policy import Policy, load_policy
from app.services.policy_view import rule_view, rule_views


def shipped_policy() -> Policy:
    return load_policy(Settings(_env_file=None).policy_file)


def test_one_view_per_rule_in_file_order() -> None:
    policy = shipped_policy()
    views = rule_views(policy)
    assert [v.name for v in views] == [r.name for r in policy.rules]
    assert len(views) >= 15


def test_conditions_hold_only_the_keys_that_are_set() -> None:
    policy = shipped_policy()
    for rule, view in zip(policy.rules, rule_views(policy), strict=True):
        assert set(view.conditions) == rule.when.model_fields_set
        assert all(value is not None for value in view.conditions.values())


def test_pass_follows_uses_diff(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        "rules:\n"
        "  - name: Sql only\n"
        "    when: {has_where: false}\n"
        "    then: block\n"
        "    reason: No where.\n"
        "  - name: Needs diff\n"
        "    when: {fraction_of_table_gt: 0.5}\n"
        "    then: review\n"
        "    reason: Most of a table.\n"
    )
    first, second = (rule_view(r) for r in load_policy(path).rules)
    assert (first.pass_, second.pass_) == ("pre", "post")
    assert first.conditions == {"has_where": False}
    assert second.conditions == {"fraction_of_table_gt": 0.5}
    assert (first.outcome, second.outcome) == ("block", "review")
