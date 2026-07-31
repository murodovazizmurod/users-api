"""Helpers for documenting error responses in OpenAPI."""

from __future__ import annotations

from app.modules.auth.schemas import ErrorResponse

_DESCRIPTIONS: dict[int, str] = {
    400: "Invalid request",
    401: "Missing, invalid or expired credentials",
    403: "Authenticated but not allowed to perform this action",
    404: "Resource not found",
    409: "Conflicts with the current state of the resource",
    422: "Request payload failed validation",
    429: "Too many attempts",
}


def error_responses(*status_codes: int) -> dict[int | str, dict]:
    """Build the ``responses`` mapping for a route.

    Keeps every endpoint's documented error shape identical to what the
    exception handlers actually return.
    """
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS.get(code, "Error")}
        for code in status_codes
    }
