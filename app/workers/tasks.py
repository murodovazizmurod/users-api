"""Scheduled maintenance tasks.

Celery workers are synchronous processes, while the data layer is async. Each
task therefore runs its coroutine through ``asyncio.run`` on a private engine
and disposes of it afterwards: asyncpg connections are bound to the event loop
that created them, so a pool shared across ``asyncio.run`` calls would hand out
connections attached to a closed loop.

An alternative for a busier system would be a second, synchronous engine
(``psycopg``) dedicated to workers. The private-engine approach keeps a single
set of models and URLs at the cost of one connection setup per run, which is
negligible for jobs that run on the order of minutes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import build_engine
from app.modules.auth.repository import RefreshTokenRepository
from app.modules.users.repository import UserRepository
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_with_session[T](operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run a coroutine that needs a database session from sync Celery code."""

    async def runner() -> T:
        engine = build_engine()
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                try:
                    return await operation(session)
                except Exception:
                    await session.rollback()
                    raise
        finally:
            await engine.dispose()

    return asyncio.run(runner())


@celery_app.task(name="app.workers.tasks.purge_unverified_users", bind=True, max_retries=3)
def purge_unverified_users(self, ttl_days: int | None = None) -> dict[str, int]:  # noqa: ANN001
    """Delete accounts that never completed verification.

    Runs on the beat schedule (hourly by default) and removes users that are
    still unverified ``UNVERIFIED_USER_TTL_DAYS`` after registration — two days
    per the specification. Deletion cascades to their verification codes and
    refresh tokens.

    Work is done in bounded batches so a large backlog cannot hold a long
    transaction open.
    """
    ttl = ttl_days if ttl_days is not None else settings.UNVERIFIED_USER_TTL_DAYS
    cutoff = datetime.now(UTC) - timedelta(days=ttl)

    async def operation(session: AsyncSession) -> int:
        repository = UserRepository(session)
        deleted = 0
        while True:
            batch = await repository.list_expired_unverified(cutoff, settings.CLEANUP_BATCH_SIZE)
            if not batch:
                break
            for user in batch:
                logger.info(
                    "Purging unverified user id=%s email=%s created_at=%s",
                    user.id,
                    user.email,
                    user.created_at,
                )
                await session.delete(user)
            await session.commit()
            deleted += len(batch)
            if len(batch) < settings.CLEANUP_BATCH_SIZE:
                break
        return deleted

    try:
        deleted = _run_with_session(operation)
    except Exception as exc:  # noqa: BLE001 - retry any transient database failure
        logger.exception("purge_unverified_users failed")
        raise self.retry(exc=exc, countdown=60) from exc

    logger.info("purge_unverified_users: removed %s user(s) created before %s", deleted, cutoff)
    return {"deleted": deleted}


@celery_app.task(name="app.workers.tasks.purge_expired_refresh_tokens")
def purge_expired_refresh_tokens(grace_days: int = 7) -> dict[str, int]:
    """Drop refresh tokens that expired more than ``grace_days`` ago.

    Housekeeping only: expired tokens are already rejected at runtime. The
    grace period keeps recent rows available for incident investigation.
    """
    cutoff = datetime.now(UTC) - timedelta(days=grace_days)

    async def operation(session: AsyncSession) -> int:
        deleted = await RefreshTokenRepository(session).delete_expired(cutoff)
        await session.commit()
        return deleted

    deleted = _run_with_session(operation)
    logger.info("purge_expired_refresh_tokens: removed %s token(s)", deleted)
    return {"deleted": deleted}
