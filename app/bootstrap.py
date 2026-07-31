"""Startup helpers that run before the application serves traffic."""

from __future__ import annotations

import logging

from app.core.config import settings
from app.db.session import session_scope
from app.modules.users.models import UserRole
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserCreate
from app.modules.users.service import UserService

logger = logging.getLogger(__name__)


async def create_first_admin() -> None:
    """Create the bootstrap administrator when none exists.

    Without this, a fresh database has no way to reach the admin-only
    endpoints. It is a no-op unless ``FIRST_ADMIN_EMAIL`` and
    ``FIRST_ADMIN_PASSWORD`` are set, and it never overwrites an existing
    account.
    """
    if not settings.FIRST_ADMIN_EMAIL or not settings.FIRST_ADMIN_PASSWORD:
        return

    async with session_scope() as session:
        repository = UserRepository(session)
        if await repository.admin_exists():
            return

        service = UserService(session)
        email = service.normalize_email(settings.FIRST_ADMIN_EMAIL)
        if await repository.get_by_email(email) is not None:
            logger.warning("FIRST_ADMIN_EMAIL %s already exists; not changing its role", email)
            return

        await service.create_user(
            UserCreate(email=email, password=settings.FIRST_ADMIN_PASSWORD),
            role=UserRole.ADMIN,
            is_verified=True,
        )
        logger.info("Bootstrap administrator created: %s", email)
