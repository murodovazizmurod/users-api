"""FastAPI application factory and entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.bootstrap import create_first_admin
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.db.session import dispose_engine, engine

logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {"name": "Authentication", "description": "Registration, login, token refresh, verification."},
    {"name": "Profile", "description": "Endpoints acting on the authenticated user."},
    {"name": "Users", "description": "User administration. Mostly restricted to admins."},
    {"name": "Health", "description": "Liveness and readiness probes."},
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start-up and shut-down hooks.

    Schema migrations are intentionally not run here: Alembic is invoked by the
    container entrypoint so a rolling deployment migrates once instead of once
    per replica.
    """
    configure_logging()
    logger.info("Starting %s (env=%s)", settings.PROJECT_NAME, settings.ENVIRONMENT)
    await create_first_admin()
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("Shutdown complete")


def create_application() -> FastAPI:
    """Build and configure the FastAPI application."""
    configure_logging()

    application = FastAPI(
        title=settings.PROJECT_NAME,
        description=settings.PROJECT_DESCRIPTION,
        version=settings.VERSION,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application)
    application.include_router(api_router)
    register_health_routes(application)
    return application


def register_exception_handlers(application: FastAPI) -> None:
    """Make every error response share one JSON shape."""

    @application.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error_code, "message": exc.message, "details": exc.details},
            headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            # Spelled out rather than taken from `status`, whose constant for
            # this code was renamed between Starlette releases.
            status_code=422,
            content={
                "error": "validation_error",
                "message": "Request payload failed validation",
                # Rebuilt field by field: raw pydantic errors can carry
                # exception objects that are not JSON serialisable.
                "details": {
                    "errors": [
                        {
                            "loc": list(err.get("loc", [])),
                            "msg": err.get("msg"),
                            "type": err.get("type"),
                        }
                        for err in exc.errors()
                    ]
                },
            },
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "http_error",
                "message": str(exc.detail),
                "details": {},
            },
            headers=getattr(exc, "headers", None),
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals to the client; the traceback goes to the logs.
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred",
                "details": {},
            },
        )


def register_health_routes(application: FastAPI) -> None:
    """Liveness and readiness probes for orchestrators."""

    @application.get(
        "/health",
        tags=["Health"],
        summary="Liveness probe",
        description="Returns 200 as long as the process is running.",
    )
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.VERSION}

    @application.get(
        "/health/ready",
        tags=["Health"],
        summary="Readiness probe",
        description="Verifies that the database is reachable before accepting traffic.",
    )
    async def readiness() -> dict[str, str]:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ready"}


app = create_application()
