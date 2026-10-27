"""Users for the admin's page: list and create."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db import users as users_db
from app.db.enums import UserRole
from app.db.models import User
from app.schemas.users import AccountPage, AccountView, CreateUserRequest
from app.services import auth as auth_service


def account_view(user: User) -> AccountView:
    return AccountView(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=UserRole(user.role),
        is_active=user.is_active,
        created_at=user.created_at,
    )


async def list_accounts(session: AsyncSession, *, limit: int, offset: int) -> AccountPage:
    users, total = await users_db.list_users(session, limit=limit, offset=offset)
    return AccountPage(
        items=[account_view(user) for user in users], total=total, limit=limit, offset=offset
    )


async def create_account(session: AsyncSession, body: CreateUserRequest) -> AccountView:
    try:
        user = await auth_service.create_user(
            session,
            email=body.email,
            display_name=body.display_name,
            role=body.role,
            password=body.password,
        )
    except auth_service.DuplicateEmailError as exc:
        raise ApiError(409, "email_taken", str(exc)) from None
    except auth_service.AccountError as exc:
        raise ApiError(422, "invalid_account", str(exc)) from None
    return account_view(user)
