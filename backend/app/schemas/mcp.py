"""What the MCP tools return. The SDK sends each as structured content plus the same JSON as text.

Every result carries the `action_id` it was recorded under (null when nothing was recorded) and a
`message` from agent_messages.py when there is something to tell the agent.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

ReadStatus = Literal["executed", "failed"]


class QueryDatabaseResult(BaseModel):
    action_id: str | None
    status: ReadStatus
    columns: list[str] = Field(default_factory=list)
    # One list per row, in column order; values are JSON-safe (dates ISO 8601, decimals as text).
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    message: str | None = None


class TableSummary(BaseModel):
    name: str
    description: str | None


class TableList(BaseModel):
    action_id: str | None
    status: ReadStatus
    tables: list[TableSummary] = Field(default_factory=list)
    message: str | None = None


class ColumnSummary(BaseModel):
    name: str
    type: str
    nullable: bool
    description: str | None


class TableDescription(BaseModel):
    action_id: str | None
    status: ReadStatus
    table: str
    description: str | None = None
    columns: list[ColumnSummary] = Field(default_factory=list)
    message: str | None = None


class ActionStatusResult(BaseModel):
    action_id: str
    # Null when the action was not found (unknown, someone else's, or not an action ID).
    status: str | None
    decision: str | None = None
    message: str | None = None
