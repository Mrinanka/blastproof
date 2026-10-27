import uuid
from typing import Literal

from pydantic import BaseModel

from app.db.enums import ConnectionProfile, SystemKind


class SystemView(BaseModel):
    id: uuid.UUID
    name: str
    kind: SystemKind
    connection_profile: ConnectionProfile


class SystemPage(BaseModel):
    items: list[SystemView]
    total: int
    limit: int
    offset: int


class ScanResult(BaseModel):
    system_id: uuid.UUID
    source: Literal["schema_scan"]
    chunks: int
