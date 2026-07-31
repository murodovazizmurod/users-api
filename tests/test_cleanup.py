"""Retention of unverified accounts.

The Celery task is a thin wrapper around the query exercised here; testing the
query keeps the suite free of a broker dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.modules.users.models import UserRole
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserCreate
from app.modules.users.service import UserService


async def _create_user_aged(
    session: AsyncSession, email: str, *, days_old: float, is_verified: bool
) -> None:
    service = UserService(session)
    user = await service.create_user(
        UserCreate(email=email, password="Str0ngPassw0rd"),
        role=UserRole.USER,
        is_verified=is_verified,
    )
    # Backdate the row: created_at is set by the database on insert.
    user.created_at = datetime.now(UTC) - timedelta(days=days_old)
    await session.commit()


async def test_only_stale_unverified_users_are_selected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _create_user_aged(session, "stale@example.com", days_old=3, is_verified=False)
        await _create_user_aged(session, "fresh@example.com", days_old=0.5, is_verified=False)
        await _create_user_aged(session, "verified@example.com", days_old=10, is_verified=True)

        cutoff = datetime.now(UTC) - timedelta(days=settings.UNVERIFIED_USER_TTL_DAYS)
        expired = await UserRepository(session).list_expired_unverified(cutoff, limit=100)

        assert [user.email for user in expired] == ["stale@example.com"]


async def test_deleting_a_user_cascades_to_their_codes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy import func, select

    from app.modules.auth.models import VerificationCode
    from app.modules.auth.service import AuthService
    from app.notifications.console import ConsoleNotifier

    async with session_factory() as session:
        service = AuthService(session, ConsoleNotifier())
        user = await service.signup(
            UserCreate(email="stale@example.com", password="Str0ngPassw0rd")
        )

        codes = await session.execute(select(func.count()).select_from(VerificationCode))
        assert codes.scalar_one() == 1

        repository = UserRepository(session)
        await repository.delete(user)
        await session.commit()

        codes = await session.execute(select(func.count()).select_from(VerificationCode))
        assert codes.scalar_one() == 0
