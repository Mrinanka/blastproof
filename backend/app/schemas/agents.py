import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.actions import UserRef

AgentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class AgentView(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    owner: UserRef


class AgentPage(BaseModel):
    items: list[AgentView]
    total: int
    limit: int
    offset: int


class CreateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: AgentName


class AgentCreated(AgentView):
    """Returned once, at creation: the only time the full key is shown."""

    key: str
    mcp_url: str
    claude_mcp_add: str  # the command that connects Claude Code to Blastproof
    agent_key_env: str  # the line for the PreToolUse hook's environment
