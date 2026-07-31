"""Data access for verification codes and refresh tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import RefreshToken, VerificationCode


class VerificationCodeRepository:
    """CRUD operations over the ``verification_codes`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, code: VerificationCode) -> VerificationCode:
        self.session.add(code)
        return code

    async def get_latest_for_user(self, user_id: uuid.UUID) -> VerificationCode | None:
        """Most recently issued code for a user, used or not."""
        stmt = (
            select(VerificationCode)
            .where(VerificationCode.user_id == user_id)
            .order_by(VerificationCode.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_active_for_user(self, user_id: uuid.UUID) -> VerificationCode | None:
        """The one code a user may currently redeem, if any."""
        stmt = (
            select(VerificationCode)
            .where(
                VerificationCode.user_id == user_id,
                VerificationCode.used_at.is_(None),
                VerificationCode.expires_at > datetime.now(UTC),
            )
            .order_by(VerificationCode.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def invalidate_active_for_user(self, user_id: uuid.UUID) -> None:
        """Burn any outstanding codes so only the newest one works."""
        await self.session.execute(
            update(VerificationCode)
            .where(VerificationCode.user_id == user_id, VerificationCode.used_at.is_(None))
            .values(used_at=datetime.now(UTC))
        )


class RefreshTokenRepository:
    """CRUD operations over the ``refresh_tokens`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, token: RefreshToken) -> RefreshToken:
        self.session.add(token)
        return token

    async def get_by_jti(self, jti: uuid.UUID) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.jti == jti)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Revoke every active session of a user (logout everywhere)."""
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        return int(result.rowcount or 0)

    async def delete_expired(self, cutoff: datetime) -> int:
        """Housekeeping: drop tokens that expired before ``cutoff``."""
        from sqlalchemy import delete  # local import keeps the module surface small

        result = await self.session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
        )
        return int(result.rowcount or 0)
