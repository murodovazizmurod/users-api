"""Shared pagination primitives."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, Field

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """Offset pagination, injected as a FastAPI dependency."""

    limit: int = DEFAULT_LIMIT
    offset: int = 0


def pagination_params(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Maximum items to return"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
) -> PaginationParams:
    """Dependency producing validated pagination parameters."""
    return PaginationParams(limit=limit, offset=offset)


class Page[T](BaseModel):
    """A single page of results together with the total match count."""

    items: list[T] = Field(description="Items on the current page")
    total: int = Field(description="Total number of items matching the query")
    limit: int = Field(description="Maximum items requested")
    offset: int = Field(description="Number of items skipped")
