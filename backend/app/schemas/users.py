import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import UserRole


class AccountView(BaseModel):
    """A user as an admin sees them. Emails appear only here and on /v1/auth/me (BE-118)."""

    id: uuid.UUID
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class AccountPage(BaseModel):
    items: list[AccountView]
    total: int
    limit: int
    offset: int


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The service checks the address, name and password rules and answers 422 invalid_account.
    email: str = Field(min_length=1, max_length=320)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)
    role: UserRole = UserRole.MEMBER
