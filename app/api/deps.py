"""Shared FastAPI dependencies: sessions, services and access control."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EmailNotVerifiedError, InvalidTokenError, PermissionDeniedError
from app.db.session import get_session
from app.modules.auth.service import AuthService
from app.modules.users.models import User
from app.modules.users.service import UserService
from app.notifications import Notifier, get_notifier

# auto_error=False so a missing header raises our own uniform error body.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


def get_notifier_dep() -> Notifier:
    return get_notifier()


def get_auth_service(
    session: SessionDep, notifier: Annotated[Notifier, Depends(get_notifier_dep)]
) -> AuthService:
    return AuthService(session, notifier)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_user(
    auth_service: AuthServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Resolve the caller from the ``Authorization: Bearer <token>`` header."""
    if credentials is None or not credentials.credentials:
        raise InvalidTokenError("Missing bearer token")
    return await auth_service.resolve_access_token(credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_verified_user(current_user: CurrentUser) -> User:
    """Require an account that has completed verification."""
    if not current_user.is_verified:
        raise EmailNotVerifiedError()
    return current_user


async def get_admin_user(current_user: CurrentUser) -> User:
    """Require an administrator account."""
    if not current_user.is_admin:
        raise PermissionDeniedError("This endpoint requires the admin role")
    return current_user


VerifiedUser = Annotated[User, Depends(get_verified_user)]
AdminUser = Annotated[User, Depends(get_admin_user)]
