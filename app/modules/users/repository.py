"""Data access for the users module.

The repository is the only place that knows about SQLAlchemy constructs; the
service layer works with models and plain values. This keeps queries testable
and makes a future storage change a local edit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PaginationParams
from app.modules.users.models import User, UserRole


@dataclass(frozen=True, slots=True)
class UserFilters:
    """Optional filters for the user listing."""

    search: str | None = None
    role: UserRole | None = None
    is_verified: bool | None = None
    is_active: bool | None = None


class UserRepository:
    """CRUD operations over the ``users`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- reads -------------------------------------------------------------
    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def email_exists(self, email: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(func.count()).select_from(User).where(User.email == email)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        result = await self.session.execute(stmt)
        return bool(result.scalar_one())

    async def phone_exists(self, phone: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(func.count()).select_from(User).where(User.phone == phone)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        result = await self.session.execute(stmt)
        return bool(result.scalar_one())

    async def admin_exists(self) -> bool:
        stmt = select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
        result = await self.session.execute(stmt)
        return bool(result.scalar_one())

    async def list_users(
        self, filters: UserFilters, pagination: PaginationParams
    ) -> tuple[list[User], int]:
        """Return one page of users plus the total number of matches."""
        stmt = self._apply_filters(select(User), filters)

        total_stmt = self._apply_filters(select(func.count()).select_from(User), filters)
        total = (await self.session.execute(total_stmt)).scalar_one()

        stmt = stmt.order_by(User.created_at.desc(), User.id.desc())
        stmt = stmt.limit(pagination.limit).offset(pagination.offset)
        items = list((await self.session.execute(stmt)).scalars().all())
        return items, int(total)

    async def list_expired_unverified(self, cutoff: datetime, limit: int) -> list[User]:
        """Unverified users created before ``cutoff``, oldest first."""
        stmt = (
            select(User)
            .where(User.is_verified.is_(False), User.created_at < cutoff)
            .order_by(User.created_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    # -- writes ------------------------------------------------------------
    def add(self, user: User) -> User:
        """Stage a new user; the caller commits."""
        self.session.add(user)
        return user

    async def delete(self, user: User) -> None:
        await self.session.delete(user)

    async def delete_by_ids(self, user_ids: list[uuid.UUID]) -> int:
        """Bulk delete used by the retention job. Returns the row count."""
        if not user_ids:
            return 0
        result = await self.session.execute(delete(User).where(User.id.in_(user_ids)))
        return int(result.rowcount or 0)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _apply_filters(stmt: Select, filters: UserFilters) -> Select:
        if filters.search:
            pattern = f"%{filters.search.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(User.email).like(pattern),
                    func.lower(func.coalesce(User.first_name, "")).like(pattern),
                    func.lower(func.coalesce(User.last_name, "")).like(pattern),
                )
            )
        if filters.role is not None:
            stmt = stmt.where(User.role == filters.role)
        if filters.is_verified is not None:
            stmt = stmt.where(User.is_verified.is_(filters.is_verified))
        if filters.is_active is not None:
            stmt = stmt.where(User.is_active.is_(filters.is_active))
        return stmt
