from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class KnowledgeSource(BaseModel):
    source: str
    kind: Literal["schema_scan", "schema_docs", "upload"]
    chunks: int
    table_names: list[str]
    updated_at: datetime


class KnowledgePage(BaseModel):
    items: list[KnowledgeSource]
    total: int
    limit: int
    offset: int


class KnowledgeChanged(BaseModel):
    """What an upload or a delete did: the source and how many chunks it now has or had."""

    source: str
    chunks: int
