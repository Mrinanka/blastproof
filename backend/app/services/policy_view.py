"""The rules file as the dashboard shows it."""

from app.schemas.policies import RuleView
from app.services.policy import Policy, Rule


def rule_view(rule: Rule) -> RuleView:
    when = rule.when
    return RuleView(
        name=rule.name,
        outcome=rule.then,
        reason=rule.reason,
        pass_="post" if when.uses_diff else "pre",
        conditions={key: getattr(when, key) for key in when.model_fields_set},
    )


def rule_views(policy: Policy) -> list[RuleView]:
    return [rule_view(rule) for rule in policy.rules]  # file order
