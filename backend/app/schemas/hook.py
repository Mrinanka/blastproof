from typing import Any, Literal

from pydantic import BaseModel, Field


class HookRequest(BaseModel):
    """What the hook script sends: the tool call, minus file bodies (the script drops them)."""

    tool_name: str = Field(min_length=1, max_length=64)
    tool_input: dict[str, Any]
    cwd: str = Field(default="", max_length=4096)
    project_dir: str | None = Field(default=None, max_length=4096)


class HookDecisionResponse(BaseModel):
    decision: Literal["allow", "deny"]
    reason: str | None
    rule: str | None
