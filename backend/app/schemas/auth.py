import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.enums import UserRole


class LoginRequest(BaseModel):
    # Not validated as an address: at login it is only an identifier.
    email: str = Field(min_length=1, max_length=320)
    # Capped so nobody can make the server hash megabytes.
    password: str = Field(min_length=1, max_length=256)


class UserView(BaseModel):
    """The signed-in user's own details (other users only ever see display_name)."""

    id: uuid.UUID
    email: str
    display_name: str
    role: UserRole


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserView
