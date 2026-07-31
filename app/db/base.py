"""Declarative base and shared column mixins."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import CHAR, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent UUID column.

    Uses PostgreSQL's native ``UUID`` type and falls back to a 36-character
    string elsewhere, so the same models run against SQLite in tests.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201 - SQLAlchemy hook
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgresUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):  # noqa: ANN001, ANN201 - SQLAlchemy hook
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):  # noqa: ANN001, ANN201 - SQLAlchemy hook
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class Base(DeclarativeBase):
    """Base class for every ORM model."""

    type_annotation_map = {uuid.UUID: GUID}


class UUIDPrimaryKeyMixin:
    """Adds a client-generatable UUID primary key.

    UUIDs keep row identifiers non-enumerable, which matters for the public
    ``/users/{id}`` routes.
    """

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Adds database-managed ``created_at`` / ``updated_at`` columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
