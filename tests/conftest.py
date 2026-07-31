"""Shared test fixtures.

The suite runs against an in-memory SQLite database so it needs no external
services. Environment variables are set before the application modules are
imported, because settings are read at import time.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-in-production")
os.environ.setdefault("FIRST_ADMIN_EMAIL", "")
os.environ.setdefault("FIRST_ADMIN_PASSWORD", "")
# Off by default so the suite is not throttled by its own repeated logins;
# tests/test_rate_limit.py switches it on for the cases that need it.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.deps import get_notifier_dep, get_rate_limiter  # noqa: E402
from app.core.rate_limit import InMemoryRateLimiter  # noqa: E402
from app.db.registry import Base  # noqa: E402
from app.db.session import build_engine, get_session  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.modules.users.models import User, UserRole  # noqa: E402
from app.modules.users.schemas import UserCreate  # noqa: E402
from app.modules.users.service import UserService  # noqa: E402

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "AdminPass123"
USER_EMAIL = "user@example.com"
USER_PASSWORD = "UserPass123"


class RecordingNotifier:
    """Test double that keeps every code it was asked to deliver."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send_verification_code(
        self, *, destination: str, code: str, link: str, expires_in_minutes: int
    ) -> None:
        self.sent.append({"destination": destination, "code": code, "link": link})

    def last_code_for(self, destination: str) -> str:
        for entry in reversed(self.sent):
            if entry["destination"] == destination:
                return entry["code"]
        raise AssertionError(f"No verification code was sent to {destination}")


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A fresh in-memory schema per test."""
    # build_engine is used rather than create_async_engine so the SQLite
    # foreign-key pragma is applied exactly as it is in the application.
    engine = build_engine(
        "sqlite+aiosqlite:///:memory:",
        # One shared connection keeps the in-memory database alive across
        # sessions within a test.
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def rate_limiter() -> InMemoryRateLimiter:
    """A limiter with counters isolated to a single test."""
    return InMemoryRateLimiter()


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    notifier: RecordingNotifier,
    rate_limiter: InMemoryRateLimiter,
) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the app with test doubles wired in."""

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as db_session:
            yield db_session

    fastapi_app.dependency_overrides[get_session] = override_get_session
    fastapi_app.dependency_overrides[get_notifier_dep] = lambda: notifier
    fastapi_app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    fastapi_app.dependency_overrides.clear()


async def _create_user(
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
    password: str,
    role: UserRole,
    is_verified: bool,
) -> User:
    async with session_factory() as db_session:
        service = UserService(db_session)
        return await service.create_user(
            UserCreate(email=email, password=password), role=role, is_verified=is_verified
        )


@pytest_asyncio.fixture
async def admin_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    return await _create_user(
        session_factory, ADMIN_EMAIL, ADMIN_PASSWORD, UserRole.ADMIN, is_verified=True
    )


@pytest_asyncio.fixture
async def regular_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    return await _create_user(
        session_factory, USER_EMAIL, USER_PASSWORD, UserRole.USER, is_verified=True
    )


async def login(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    """Log in and return the token pair."""
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def auth_header(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, admin_user: User) -> dict[str, str]:
    tokens = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    return auth_header(tokens["access_token"])


@pytest_asyncio.fixture
async def user_headers(client: AsyncClient, regular_user: User) -> dict[str, str]:
    tokens = await login(client, USER_EMAIL, USER_PASSWORD)
    return auth_header(tokens["access_token"])
