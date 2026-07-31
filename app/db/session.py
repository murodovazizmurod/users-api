"""Async engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def build_engine(url: str | None = None, **overrides: Any) -> AsyncEngine:
    """Create an async engine configured for the target dialect.

    Exposed as a function because Celery tasks build a short-lived engine of
    their own (see ``app.workers.tasks``).
    """
    url = url or settings.DATABASE_URL
    kwargs: dict[str, Any] = {"echo": settings.DB_ECHO, "pool_pre_ping": True, "future": True}
    if not url.startswith("sqlite"):
        # SQLite's async driver does not support these pooling options.
        kwargs |= {"pool_size": settings.DB_POOL_SIZE, "max_overflow": settings.DB_MAX_OVERFLOW}
    kwargs |= overrides

    new_engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        _enforce_sqlite_foreign_keys(new_engine)
    return new_engine


def _enforce_sqlite_foreign_keys(target: AsyncEngine) -> None:
    """Turn on foreign key enforcement for SQLite connections.

    SQLite ships with foreign keys disabled, so ``ON DELETE CASCADE`` would be
    silently ignored and deleting a user would leave orphaned verification
    codes and refresh tokens behind.
    """

    @event.listens_for(target.sync_engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


engine: AsyncEngine = build_engine()

session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session.

    The session is rolled back and closed automatically; services decide when
    to commit.
    """
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager for code running outside the request cycle."""
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close every pooled connection (called on application shutdown)."""
    await engine.dispose()
