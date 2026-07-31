"""Authentication-related ORM models: verification codes and refresh tokens."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.users.models import User


class VerificationChannel(enum.StrEnum):
    """Delivery channel of a one-time verification code."""

    EMAIL = "email"
    SMS = "sms"


verification_channel_enum = SAEnum(
    VerificationChannel,
    name="verification_channel",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class VerificationCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single-use, time-limited code confirming ownership of an address.

    Only the hash of the code is stored, so a database dump cannot be used to
    verify accounts.
    """

    __tablename__ = "verification_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel: Mapped[VerificationChannel] = mapped_column(
        verification_channel_enum, default=VerificationChannel.EMAIL, nullable=False
    )
    # Email address or phone number the code was sent to, kept for auditing.
    destination: Mapped[str] = mapped_column(String(320), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped[User] = relationship(back_populates="verification_codes")

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_expired(self) -> bool:
        return _as_utc(self.expires_at) <= datetime.now(UTC)

    @property
    def is_usable(self) -> bool:
        return not self.is_used and not self.is_expired


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Server-side record of an issued refresh token.

    JWTs are stateless, so revocation needs a stored counterpart. Persisting
    the token id lets us rotate on every refresh and invalidate a whole session
    on logout or on detection of a replayed token.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # The JWT "jti" claim; unique so a replayed token cannot be re-registered.
    jti: Mapped[uuid.UUID] = mapped_column(GUID(), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when this token is rotated, pointing at its replacement.
    replaced_by_jti: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and _as_utc(self.expires_at) > datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Treat naive timestamps as UTC.

    SQLite drops timezone information on round-trip, so values read back from
    a test database arrive naive while PostgreSQL returns them aware.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)
