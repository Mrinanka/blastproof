from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.auth import CurrentUser, get_app_settings, get_current_user, jwt_secret
from app.core.errors import ApiError, ErrorResponse
from app.core.security import create_access_token
from app.db.enums import UserRole
from app.db.session import get_session
from app.schemas.auth import LoginRequest, LoginResponse, UserView
from app.services import auth as auth_service

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def login(
    body: LoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> LoginResponse:
    """Email and password in, an 8-hour token out. Rate limited per email+IP and per email."""
    secret = jwt_secret(settings)
    ip = auth_service.client_ip(request.client.host if request.client else None)
    try:
        user = await auth_service.authenticate(session, body.email, body.password, ip)
    except auth_service.TooManyAttemptsError as exc:
        raise ApiError(
            429,
            "too_many_attempts",
            "Too many failed logins. Try again later.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None
    except auth_service.InvalidCredentialsError:
        raise ApiError(
            401,
            "invalid_credentials",
            "Email or password is incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    token, expires_at = create_access_token(user.id, user.token_version, secret)
    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user=UserView(
            id=user.id, email=user.email, display_name=user.display_name, role=UserRole(user.role)
        ),
    )


@router.get("/me", response_model=UserView, responses={401: {"model": ErrorResponse}})
async def me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> UserView:
    """The signed-in user's ID, name, email and role."""
    return UserView(id=user.id, email=user.email, display_name=user.display_name, role=user.role)
