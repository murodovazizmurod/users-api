"""Rate limiting on the unauthenticated endpoints.

The suite runs with rate limiting disabled globally so repeated logins in other
tests are not throttled; here it is switched back on per test by pointing the
routes at a real in-memory limiter and restoring the setting.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.rate_limit import (
    InMemoryRateLimiter,
    NullRateLimiter,
    RateLimitRule,
    build_rate_limiter,
)
from app.modules.users.models import User

LOGIN = {"email": "user@example.com", "password": "UserPass123"}
WRONG = {"email": "user@example.com", "password": "WrongPass123"}


@pytest.fixture(autouse=True)
def _enable_rate_limiting(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)


# ---------------------------------------------------------------------------
# The limiter itself
# ---------------------------------------------------------------------------
async def test_in_memory_limiter_counts_and_resets() -> None:
    limiter = InMemoryRateLimiter()
    rule = RateLimitRule(limit=2, window_seconds=60)

    assert (await limiter.peek("k", rule)).count == 0
    assert (await limiter.hit("k", rule)).count == 1
    state = await limiter.hit("k", rule)
    assert state.count == 2
    assert state.remaining == 0
    assert not state.exceeded

    assert (await limiter.hit("k", rule)).exceeded is True

    await limiter.reset("k")
    assert (await limiter.peek("k", rule)).count == 0


async def test_in_memory_limiter_forgets_after_the_window(monkeypatch) -> None:
    limiter = InMemoryRateLimiter()
    rule = RateLimitRule(limit=1, window_seconds=60)
    await limiter.hit("k", rule)

    # Advance the clock past the window instead of sleeping.
    import app.core.rate_limit as module

    base = module.time.monotonic()
    monkeypatch.setattr(module.time, "monotonic", lambda: base + 61)

    assert (await limiter.peek("k", rule)).count == 0


async def test_limiter_selection_follows_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    assert isinstance(build_rate_limiter(), NullRateLimiter)

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIS_URL", "")
    assert isinstance(build_rate_limiter(), InMemoryRateLimiter)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
async def test_failed_logins_are_throttled(
    client: AsyncClient, regular_user: User, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES", 3)

    for _ in range(3):
        assert (await client.post("/auth/login", json=WRONG)).status_code == 401

    blocked = await client.post("/auth/login", json=WRONG)
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "too_many_attempts"
    assert "Retry-After" in blocked.headers


async def test_throttling_also_blocks_the_correct_password(
    client: AsyncClient, regular_user: User, monkeypatch
) -> None:
    """Once the budget is gone, a guessed-right password must not slip through."""
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES", 2)

    for _ in range(2):
        await client.post("/auth/login", json=WRONG)

    assert (await client.post("/auth/login", json=LOGIN)).status_code == 429


async def test_successful_login_clears_the_counter(
    client: AsyncClient, regular_user: User, monkeypatch
) -> None:
    """A legitimate user must not be locked out by their own typos."""
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES", 3)

    for _ in range(2):
        await client.post("/auth/login", json=WRONG)

    assert (await client.post("/auth/login", json=LOGIN)).status_code == 200

    # Budget is back to full, so two more failures are still accepted.
    for _ in range(2):
        assert (await client.post("/auth/login", json=WRONG)).status_code == 401


async def test_the_budget_is_per_email(
    client: AsyncClient, regular_user: User, admin_user: User, monkeypatch
) -> None:
    """Throttling one account must not lock everybody else out."""
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES", 2)

    for _ in range(3):
        await client.post("/auth/login", json=WRONG)
    assert (await client.post("/auth/login", json=WRONG)).status_code == 429

    other = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "AdminPass123"}
    )
    assert other.status_code == 200


# ---------------------------------------------------------------------------
# Signup and code resend
# ---------------------------------------------------------------------------
async def test_registrations_are_throttled(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SIGNUP_MAX_REQUESTS", 2)

    for index in range(2):
        response = await client.post(
            "/auth/signup",
            json={"email": f"new{index}@example.com", "password": "Str0ngPassw0rd"},
        )
        assert response.status_code == 201

    blocked = await client.post(
        "/auth/signup", json={"email": "new3@example.com", "password": "Str0ngPassw0rd"}
    )
    assert blocked.status_code == 429


async def test_code_resends_are_throttled(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "RESEND_MAX_REQUESTS", 2)
    await client.post(
        "/auth/signup", json={"email": "ada@example.com", "password": "Str0ngPassw0rd"}
    )

    for _ in range(2):
        response = await client.post("/auth/verify/resend", json={"email": "ada@example.com"})
        assert response.status_code == 200

    blocked = await client.post("/auth/verify/resend", json={"email": "ada@example.com"})
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "too_many_attempts"


async def test_resend_throttling_is_per_address(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "RESEND_MAX_REQUESTS", 1)

    assert (
        await client.post("/auth/verify/resend", json={"email": "one@example.com"})
    ).status_code == 200
    assert (
        await client.post("/auth/verify/resend", json={"email": "one@example.com"})
    ).status_code == 429
    assert (
        await client.post("/auth/verify/resend", json={"email": "two@example.com"})
    ).status_code == 200
