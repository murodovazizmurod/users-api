"""User ORM model."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    # Imported for typing only: app.modules.auth.models imports this module,
    # so a runtime import would be circular. SQLAlchemy resolves the related
    # classes through its own registry.
    from app.modules.auth.models import RefreshToken, VerificationCode


class UserRole(enum.StrEnum):
    """Access level of an account."""

    USER = "user"
    ADMIN = "admin"


user_role_enum = SAEnum(
    UserRole,
    name="user_role",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A registered account."""

    __tablename__ = "users"
    __table_args__ = (
        # Supports the retention job's "unverified and older than N days" scan.
        Index("ix_users_unverified_created_at", "is_verified", "created_at"),
    )

    # Stored lower-cased and unique; see UserService.normalize_email.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Optional; required only when verification runs over SMS.
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)

    role: Mapped[UserRole] = mapped_column(
        user_role_enum, default=UserRole.USER, nullable=False, index=True
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Soft switch for administrators to suspend an account without deleting it.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # passive_deletes leaves the cascade to the database's ON DELETE CASCADE
    # instead of loading every child row first; lazy="raise" keeps these
    # collections from being loaded accidentally on list endpoints, where they
    # would turn one query into three.
    verification_codes: Mapped[list[VerificationCode]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )

    @property
    def full_name(self) -> str | None:
        """Human-readable name, or ``None`` when neither part was provided."""
        parts = [part for part in (self.first_name, self.last_name) if part]
        return " ".join(parts) or None

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<User id={self.id} email={self.email!r} role={self.role.value}>"
