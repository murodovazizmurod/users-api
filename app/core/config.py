"""Application settings.

All configuration is read from environment variables (or a local ``.env`` file)
so the same image can be promoted across environments without a rebuild.
"""

from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "prod"]


class Settings(BaseSettings):
    """Typed application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- General ----------------------------------------------------------
    PROJECT_NAME: str = "Users API"
    PROJECT_DESCRIPTION: str = (
        "User management module: registration, authentication, verification, "
        "role-based access control and user administration."
    )
    VERSION: str = "1.0.0"
    ENVIRONMENT: Environment = "dev"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Path prefix for every route. Empty by default so the endpoints match the
    # specification exactly (``/auth/login``, ``/me``, ``/users``). Set it to
    # ``/api/v1`` to serve a versioned API without touching the routers.
    API_PREFIX: str = ""

    # Comma-separated list of allowed CORS origins ("*" allows everything).
    CORS_ORIGINS: str = "*"

    # --- Database ---------------------------------------------------------
    # Any SQLAlchemy async URL. PostgreSQL (asyncpg) is the default; SQLite
    # (aiosqlite) is supported for a zero-dependency local run and for tests.
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/users_api"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # --- Security ---------------------------------------------------------
    SECRET_KEY: str = "insecure-development-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "users-api"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 30

    # --- Verification -----------------------------------------------------
    VERIFICATION_CHANNEL: Literal["email", "sms"] = "email"
    VERIFICATION_CODE_LENGTH: int = Field(default=6, ge=4, le=10)
    VERIFICATION_CODE_TTL_MINUTES: int = 15
    VERIFICATION_MAX_ATTEMPTS: int = 5
    # Public base URL used to build the confirmation link shown next to the code.
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # --- Retention / cleanup ---------------------------------------------
    # Users that stay unverified for longer than this are deleted by Celery.
    UNVERIFIED_USER_TTL_DAYS: int = 2
    CLEANUP_INTERVAL_MINUTES: int = 60
    CLEANUP_BATCH_SIZE: int = 1000

    # --- Celery -----------------------------------------------------------
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    CELERY_TIMEZONE: str = "UTC"

    # --- Bootstrap admin --------------------------------------------------
    # Optional: a verified admin created on startup if the table has no admin.
    # Without it there is no way to reach the admin-only endpoints on a fresh
    # database.
    FIRST_ADMIN_EMAIL: EmailStr | None = None
    FIRST_ADMIN_PASSWORD: str | None = None

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @field_validator("API_PREFIX", mode="after")
    @classmethod
    def _normalise_prefix(cls, value: str) -> str:
        """Accept ``api/v1``, ``/api/v1`` or ``/api/v1/`` alike."""
        value = value.strip().rstrip("/")
        if value and not value.startswith("/"):
            value = f"/{value}"
        return value

    @field_validator("FIRST_ADMIN_EMAIL", "FIRST_ADMIN_PASSWORD", mode="before")
    @classmethod
    def _empty_string_is_unset(cls, value: str | None) -> str | None:
        """Treat ``KEY=`` in a .env file as "not configured"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Fail fast instead of shipping the development secret to production."""
        if self.ENVIRONMENT == "prod" and "insecure" in self.SECRET_KEY:
            raise ValueError("SECRET_KEY must be overridden when ENVIRONMENT=prod")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()
