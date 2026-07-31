"""Fixed-window rate limiting for the unauthenticated endpoints.

Two implementations sit behind one protocol, mirroring the notification layer:
Redis for real deployments, where every replica must share one budget, and an
in-process fallback for a single-process local run and for tests.

The window is fixed rather than sliding: the first hit sets the expiry and the
counter resets when it lapses. That trades some precision at a window boundary
for one round trip per request and no stored history, which is the right shape
for abuse prevention.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings
from app.core.exceptions import TooManyAttemptsError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """How many hits are allowed in a window, and how long the window is."""

    limit: int
    window_seconds: int

    @property
    def retry_after(self) -> int:
        return self.window_seconds


@dataclass(frozen=True, slots=True)
class RateLimitState:
    """Result of recording or inspecting a hit."""

    count: int
    rule: RateLimitRule

    @property
    def exceeded(self) -> bool:
        return self.count > self.rule.limit

    @property
    def remaining(self) -> int:
        return max(0, self.rule.limit - self.count)


class RateLimiter(Protocol):
    """Counts events per key within a fixed window."""

    async def hit(self, key: str, rule: RateLimitRule) -> RateLimitState:
        """Record one event and return the resulting state."""
        ...

    async def peek(self, key: str, rule: RateLimitRule) -> RateLimitState:
        """Read the current count without recording anything."""
        ...

    async def reset(self, key: str) -> None:
        """Drop the counter, e.g. after a successful login."""
        ...


class NullRateLimiter:
    """No-op limiter used when rate limiting is switched off."""

    async def hit(self, key: str, rule: RateLimitRule) -> RateLimitState:
        return RateLimitState(count=0, rule=rule)

    async def peek(self, key: str, rule: RateLimitRule) -> RateLimitState:
        return RateLimitState(count=0, rule=rule)

    async def reset(self, key: str) -> None:
        return None


class InMemoryRateLimiter:
    """Per-process counters.

    Adequate for a single-process local run and for tests. It does not
    coordinate across workers or replicas, so a deployment should point
    ``RATE_LIMIT_REDIS_URL`` at Redis instead.
    """

    def __init__(self) -> None:
        self._counters: dict[str, tuple[int, float]] = {}

    def _current(self, key: str) -> tuple[int, float] | None:
        entry = self._counters.get(key)
        if entry is None:
            return None
        if entry[1] <= time.monotonic():
            del self._counters[key]
            return None
        return entry

    async def hit(self, key: str, rule: RateLimitRule) -> RateLimitState:
        entry = self._current(key)
        if entry is None:
            self._counters[key] = (1, time.monotonic() + rule.window_seconds)
            return RateLimitState(count=1, rule=rule)
        count, expires_at = entry
        self._counters[key] = (count + 1, expires_at)
        return RateLimitState(count=count + 1, rule=rule)

    async def peek(self, key: str, rule: RateLimitRule) -> RateLimitState:
        entry = self._current(key)
        return RateLimitState(count=entry[0] if entry else 0, rule=rule)

    async def reset(self, key: str) -> None:
        self._counters.pop(key, None)


class RedisRateLimiter:
    """Counters shared by every replica through Redis.

    Failures are handled fail-open: if Redis is unreachable the request is
    allowed and a warning is logged. Losing the broker should degrade abuse
    protection, not take authentication offline.
    """

    def __init__(self, url: str) -> None:
        from redis.asyncio import Redis  # imported lazily so tests need no redis

        self._client = Redis.from_url(url, decode_responses=True)

    async def hit(self, key: str, rule: RateLimitRule) -> RateLimitState:
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                # nx=True sets the expiry only on the first hit, so the window
                # runs from the first event instead of sliding on every one.
                pipe.expire(key, rule.window_seconds, nx=True)
                count, _ = await pipe.execute()
            return RateLimitState(count=int(count), rule=rule)
        except Exception as exc:  # noqa: BLE001 - any Redis failure must fail open
            logger.warning("Rate limiter unavailable, allowing request: %s", exc)
            return RateLimitState(count=0, rule=rule)

    async def peek(self, key: str, rule: RateLimitRule) -> RateLimitState:
        try:
            value = await self._client.get(key)
            return RateLimitState(count=int(value) if value else 0, rule=rule)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rate limiter unavailable, allowing request: %s", exc)
            return RateLimitState(count=0, rule=rule)

    async def reset(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rate limiter reset failed: %s", exc)

    async def close(self) -> None:
        await self._client.aclose()


def build_rate_limiter() -> RateLimiter:
    """Select the limiter implied by the current configuration."""
    if not settings.RATE_LIMIT_ENABLED:
        logger.info("Rate limiting is disabled")
        return NullRateLimiter()
    if settings.RATE_LIMIT_REDIS_URL:
        logger.info("Rate limiting backed by Redis")
        return RedisRateLimiter(settings.RATE_LIMIT_REDIS_URL)
    logger.warning("Rate limiting is using per-process counters, not shared across replicas")
    return InMemoryRateLimiter()


async def enforce(limiter: RateLimiter, key: str, rule: RateLimitRule, message: str) -> None:
    """Reject the request when ``key`` has already used up its budget.

    Only inspects the counter; call ``limiter.hit`` separately to record the
    event. Keeping the two apart lets the login route charge failures only.
    """
    state = await limiter.peek(key, rule)
    if state.count >= rule.limit:
        raise TooManyAttemptsError(message, headers={"Retry-After": str(rule.retry_after)})


# Rules resolved from settings at import time.
def login_rule() -> RateLimitRule:
    return RateLimitRule(settings.LOGIN_MAX_FAILURES, settings.LOGIN_FAILURE_WINDOW_SECONDS)


def signup_rule() -> RateLimitRule:
    return RateLimitRule(settings.SIGNUP_MAX_REQUESTS, settings.SIGNUP_WINDOW_SECONDS)


def resend_rule() -> RateLimitRule:
    return RateLimitRule(settings.RESEND_MAX_REQUESTS, settings.RESEND_WINDOW_SECONDS)
