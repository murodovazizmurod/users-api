"""Aggregates every module router behind the configured API prefix."""

from fastapi import APIRouter

from app.core.config import settings
from app.modules.auth.router import router as auth_router
from app.modules.users.router import me_router
from app.modules.users.router import router as users_router

# APIRouter rejects an empty string, so the prefix is only passed when set.
api_router = APIRouter(prefix=settings.API_PREFIX) if settings.API_PREFIX else APIRouter()
api_router.include_router(auth_router)
api_router.include_router(me_router)
api_router.include_router(users_router)
