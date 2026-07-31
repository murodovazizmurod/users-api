"""Tests for the scheduled retention tasks.

These exercise the Celery task functions themselves rather than the query
underneath them, so the ``asyncio.run`` wrapper, the private engine and the
batching loop are all covered. Tasks are invoked with ``.apply()``, which runs
them eagerly in-process and needs no broker.

The tasks build their own engine from ``settings.DATABASE_URL``, so each test
points that at a temporary SQLite file: an in-memory database would be empty
from the task's perspective, since it opens its own connection.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.registry import Base
from app.db.session import build_engine
from app.modules.auth.models import RefreshToken, VerificationCode
from app.modules.users.models import User, UserRole
from app.modules.users.schemas import UserCreate
from app.modules.users.service import UserService
from app.workers.tasks import purge_expired_refresh_tokens, purge_unverified_users


@pytest.fixture
def file_database(tmp_path, monkeypatch):
    """Point the application at a temporary SQLite file and create the schema."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'retention.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)

    async def create_schema() -> None:
        engine = build_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    return url


def run_async(url: str, operation):
    """Run a coroutine factory against a short-lived session on ``url``."""

    async def runner():
        engine = build_engine(url)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                return await operation(session)
        finally:
            await engine.dispose()

    return asyncio.run(runner())


async def _seed_user(session, email: str, *, days_old: float, is_verified: bool) -> User:
    user = await UserService(session).create_user(
        UserCreate(email=email, password="Str0ngPassw0rd"),
        role=UserRole.USER,
        is_verified=is_verified,
    )
    # created_at is assigned by the database on insert, so backdate afterwards.
    user.created_at = datetime.now(UTC) - timedelta(days=days_old)
    await session.commit()
    return user


def test_purge_deletes_only_stale_unverified_accounts(file_database):
    async def seed(session):
        await _seed_user(session, "stale@example.com", days_old=3, is_verified=False)
        await _seed_user(session, "just.inside@example.com", days_old=1.9, is_verified=False)
        await _seed_user(session, "old.but.verified@example.com", days_old=30, is_verified=True)

    run_async(file_database, seed)

    result = purge_unverified_users.apply().get()
    assert result == {"deleted": 1}

    async def remaining(session):
        rows = await session.execute(select(User.email).order_by(User.email))
        return sorted(rows.scalars().all())

    assert run_async(file_database, remaining) == [
        "just.inside@example.com",
        "old.but.verified@example.com",
    ]


def test_purge_respects_the_configured_retention_window(file_database, monkeypatch):
    """A user one day old survives the default window but not a zero-day one."""

    async def seed(session):
        await _seed_user(session, "yesterday@example.com", days_old=1, is_verified=False)

    run_async(file_database, seed)

    assert purge_unverified_users.apply().get() == {"deleted": 0}
    assert purge_unverified_users.apply(kwargs={"ttl_days": 0}).get() == {"deleted": 1}


def test_purge_walks_through_multiple_batches(file_database, monkeypatch):
    """The batching loop must drain a backlog larger than one batch."""
    monkeypatch.setattr(settings, "CLEANUP_BATCH_SIZE", 2)

    async def seed(session):
        for index in range(5):
            await _seed_user(session, f"stale{index}@example.com", days_old=5, is_verified=False)

    run_async(file_database, seed)

    assert purge_unverified_users.apply().get() == {"deleted": 5}


def test_purge_cascades_to_codes_and_sessions(file_database):
    """Deleting a user must not leave orphaned child rows behind."""

    async def seed(session):
        user = await _seed_user(session, "stale@example.com", days_old=5, is_verified=False)
        session.add(
            VerificationCode(
                user_id=user.id,
                destination=user.email,
                code_hash="irrelevant",
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
        )
        session.add(
            RefreshToken(
                user_id=user.id,
                jti=uuid.uuid4(),
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()

    run_async(file_database, seed)

    assert purge_unverified_users.apply().get() == {"deleted": 1}

    async def orphans(session):
        codes = await session.execute(select(func.count()).select_from(VerificationCode))
        tokens = await session.execute(select(func.count()).select_from(RefreshToken))
        return codes.scalar_one(), tokens.scalar_one()

    assert run_async(file_database, orphans) == (0, 0)


def test_purge_on_an_empty_database_is_a_no_op(file_database):
    assert purge_unverified_users.apply().get() == {"deleted": 0}


def test_expired_refresh_tokens_are_removed_after_the_grace_period(file_database):
    async def seed(session):
        user = await _seed_user(session, "active@example.com", days_old=0, is_verified=True)
        session.add(
            RefreshToken(
                user_id=user.id,
                jti=uuid.uuid4(),
                expires_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        session.add(
            RefreshToken(
                user_id=user.id,
                jti=uuid.uuid4(),
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()

    run_async(file_database, seed)

    assert purge_expired_refresh_tokens.apply(kwargs={"grace_days": 7}).get() == {"deleted": 1}

    async def remaining(session):
        rows = await session.execute(select(func.count()).select_from(RefreshToken))
        return rows.scalar_one()

    assert run_async(file_database, remaining) == 1
