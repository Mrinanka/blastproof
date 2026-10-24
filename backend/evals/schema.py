"""The shape of the eval scenario files (`evals/scenarios/*.yaml`, backend plan §8 step 3).

Two kinds of entry:
- `sql`: a statement an agent sends to `query_database`, with the decision it should get and, where
  known, the facts the shadow run should measure against the seed as of `anchor_date`.
- `hook`: a Claude Code tool call (the PreToolUse `tool_name` and `tool_input`) the hook must judge.

Labels are the correct outcome, not what today's rules happen to produce, so a mismatch shows up
in the eval metrics as a miss or a false block.
"""

from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    StringConstraints,
    model_validator,
)

ScenarioId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
# "table.column" for breakdowns, "table.group" for derived groups (D-023).
QualifiedName = Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TableChange(_Strict):
    deleted: NonNegativeInt = 0
    updated: NonNegativeInt = 0
    inserted: NonNegativeInt = 0


class ExpectedImpact(_Strict):
    table_changes: dict[str, TableChange]
    breakdowns: dict[QualifiedName, dict[str, NonNegativeInt]] = {}
    derived_groups: dict[QualifiedName, NonNegativeInt] = {}


class SqlScenario(_Strict):
    kind: Literal["sql"]
    id: ScenarioId
    incident: str | None = None
    sql: NonEmpty
    intent: NonEmpty
    expected_decision: Literal["allow", "review", "block"]
    # Rules from backend plan §9 that must match it, for the rules step to check. Empty when no
    # rule should match (a write no rule allows goes to review) or the judge must decide.
    expected_rules: list[NonEmpty] = []
    # True when the SQL alone settles it, so pass 1 blocks it before any shadow run (DW-B09).
    decidable_from_sql: bool
    expected_impact: ExpectedImpact | None = None
    notes: str | None = None


class HookScenario(_Strict):
    kind: Literal["hook"]
    id: ScenarioId
    incident: str
    tool_name: NonEmpty
    tool_input: dict[str, Any]
    expected_decision: Literal["allow", "deny"]
    notes: str | None = None


Scenario = Annotated[SqlScenario | HookScenario, Field(discriminator="kind")]


class ScenarioFile(_Strict):
    anchor_date: date
    scenarios: Annotated[list[Scenario], Field(min_length=1)]

    @model_validator(mode="after")
    def _unique_ids(self) -> "ScenarioFile":
        seen: set[str] = set()
        for scenario in self.scenarios:
            if scenario.id in seen:
                raise ValueError(f"duplicate scenario id: {scenario.id}")
            seen.add(scenario.id)
        return self


def load_scenario_file(path: Path) -> ScenarioFile:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ScenarioFile.model_validate(data)
