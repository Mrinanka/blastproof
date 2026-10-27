from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.db.enums import Decision


class RuleView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    outcome: Decision
    reason: str
    pass_: Literal["pre", "post"] = Field(alias="pass")
    # Only the conditions the rule sets, as written in rules.yaml.
    conditions: dict[str, JsonValue]


class RulePage(BaseModel):
    items: list[RuleView]
    total: int
    limit: int
    offset: int
