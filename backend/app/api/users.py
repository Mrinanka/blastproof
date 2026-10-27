from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams
from app.core.auth import CurrentUser, require_admin
from app.core.errors import ErrorResponse
from app.db.session import get_session, get_write_session
from app.schemas.users import AccountPage, AccountView, CreateUserRequest
from app.services import user_admin

router = APIRouter(prefix="/v1/users", tags=["users"])

_ERRORS = {code: {"model": ErrorResponse} for code in (401, 403, 422)}


@router.get("", response_model=AccountPage, responses=_ERRORS)
async def list_users(
    page: Annotated[PageParams, Query()],
    _admin: Annotated[CurrentUser, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountPage:
    """Every user, newest first (admins only). The only list that shows email addresses."""
    return await user_admin.list_accounts(session, limit=page.limit, offset=page.offset)


@router.post(
    "",
    response_model=AccountView,
    status_code=201,
    responses=_ERRORS | {409: {"model": ErrorResponse}},
)
async def create_user(
    body: CreateUserRequest,
    _admin: Annotated[CurrentUser, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_write_session)],
) -> AccountView:
    """Add a user (admins only). The email must not be taken, ignoring case."""
    return await user_admin.create_account(session, body)
