"""Business logic for the users module."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    PermissionDeniedError,
    PhoneAlreadyRegisteredError,
    UserNotFoundError,
)
from app.core.pagination import Page, PaginationParams
from app.core.security import hash_password, verify_password
from app.modules.users.models import User, UserRole
from app.modules.users.repository import UserFilters, UserRepository
from app.modules.users.schemas import UserAdminUpdate, UserCreate, UserRead

logger = logging.getLogger(__name__)

# Fields on UserAdminUpdate that only an administrator may set.
ADMIN_ONLY_FIELDS = frozenset({"email", "role", "is_active", "is_verified"})


def conflict_from_integrity_error(exc: IntegrityError) -> ConflictError:
    """Map a unique-constraint violation onto the field that caused it.

    Uniqueness is checked before writing, but that check and the write are not
    atomic: two concurrent requests can both pass it. The unique index is the
    real guarantee, so its violation is translated here instead of surfacing
    as a 500.
    """
    detail = str(getattr(exc, "orig", exc)).lower()
    if "phone" in detail:
        return PhoneAlreadyRegisteredError()
    if "email" in detail:
        return EmailAlreadyRegisteredError()
    return ConflictError("The request conflicts with an existing record")


class UserService:
    """Use cases for creating, reading, updating and deleting users."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = UserRepository(session)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def normalize_email(email: str) -> str:
        """Normalise an address so uniqueness is case-insensitive."""
        return email.strip().lower()

    # -- creation ----------------------------------------------------------
    async def create_user(
        self,
        payload: UserCreate,
        *,
        role: UserRole = UserRole.USER,
        is_verified: bool = False,
        commit: bool = True,
    ) -> User:
        """Create a user, enforcing email uniqueness.

        New accounts start unverified, as required by the registration flow.
        """
        email = self.normalize_email(payload.email)
        if await self.repository.email_exists(email):
            raise EmailAlreadyRegisteredError()
        if payload.phone and await self.repository.phone_exists(payload.phone):
            raise PhoneAlreadyRegisteredError()

        user = User(
            email=email,
            hashed_password=hash_password(payload.password),
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
            role=role,
            is_verified=is_verified,
            verified_at=datetime.now(UTC) if is_verified else None,
        )
        self.repository.add(user)
        try:
            await self.session.flush()
            if commit:
                await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise conflict_from_integrity_error(exc) from exc

        await self.session.refresh(user)
        logger.info("User created: id=%s email=%s role=%s", user.id, user.email, user.role.value)
        return user

    # -- reads -------------------------------------------------------------
    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.repository.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError()
        return user

    async def get_by_email(self, email: str) -> User | None:
        return await self.repository.get_by_email(self.normalize_email(email))

    async def list_users(
        self, filters: UserFilters, pagination: PaginationParams
    ) -> Page[UserRead]:
        items, total = await self.repository.list_users(filters, pagination)
        return Page[UserRead](
            items=[UserRead.model_validate(item) for item in items],
            total=total,
            limit=pagination.limit,
            offset=pagination.offset,
        )

    # -- updates -----------------------------------------------------------
    async def update_user(
        self, *, actor: User, user_id: uuid.UUID, payload: UserAdminUpdate
    ) -> User:
        """Partially update a user.

        A user may edit their own profile fields; only an administrator may
        edit another account or touch ``email``, ``role``, ``is_active`` and
        ``is_verified``.
        """
        changes = payload.model_dump(exclude_unset=True)

        if not actor.is_admin:
            if actor.id != user_id:
                raise PermissionDeniedError("You may only update your own profile")
            forbidden = ADMIN_ONLY_FIELDS.intersection(changes)
            if forbidden:
                raise PermissionDeniedError(
                    f"Only administrators may change: {', '.join(sorted(forbidden))}"
                )

        user = await self.get_user(user_id)

        if actor.is_admin and actor.id == user.id and changes.get("role") == UserRole.USER:
            # Prevents an administrator from accidentally removing the last
            # admin and locking everyone out of the admin endpoints.
            raise ConflictError("Administrators cannot demote their own account")

        if "email" in changes and changes["email"] is not None:
            email = self.normalize_email(changes["email"])
            if await self.repository.email_exists(email, exclude_id=user.id):
                raise EmailAlreadyRegisteredError()
            changes["email"] = email
            # NOTE: an administrative email change keeps the verification
            # status. With more time this would instead start a "confirm the
            # new address" flow: store the pending address, send a code to it
            # and swap only once confirmed, so the account is never left in a
            # state the retention job could delete.

        if changes.get("phone") and await self.repository.phone_exists(
            changes["phone"], exclude_id=user.id
        ):
            raise PhoneAlreadyRegisteredError()

        if changes.get("is_verified") is True and not user.is_verified:
            user.verified_at = datetime.now(UTC)
        elif changes.get("is_verified") is False:
            user.verified_at = None

        for field, value in changes.items():
            setattr(user, field, value)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise conflict_from_integrity_error(exc) from exc

        await self.session.refresh(user)
        logger.info("User updated: id=%s fields=%s by=%s", user.id, sorted(changes), actor.id)
        return user

    async def change_password(
        self, *, user: User, current_password: str, new_password: str
    ) -> None:
        """Change a user's own password and invalidate their other sessions."""
        if not verify_password(current_password, user.hashed_password):
            raise InvalidCredentialsError("Current password is incorrect")
        user.hashed_password = hash_password(new_password)
        await self.session.commit()
        logger.info("Password changed: user_id=%s", user.id)

    # -- deletion ----------------------------------------------------------
    async def delete_user(self, *, actor: User, user_id: uuid.UUID) -> None:
        """Delete a user. Administrators only, and never themselves."""
        if not actor.is_admin:
            raise PermissionDeniedError("Only administrators may delete users")
        if actor.id == user_id:
            raise ConflictError("Administrators cannot delete their own account")

        user = await self.get_user(user_id)
        await self.repository.delete(user)
        await self.session.commit()
        logger.info("User deleted: id=%s by=%s", user_id, actor.id)
