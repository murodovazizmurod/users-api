"""Celery application and beat schedule."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab  # noqa: F401 - handy for cron-style schedules

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()

celery_app = Celery(
    "users_api",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    timezone=settings.CELERY_TIMEZONE,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=270,
    worker_max_tasks_per_child=200,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    beat_schedule={
        "purge-unverified-users": {
            "task": "app.workers.tasks.purge_unverified_users",
            "schedule": settings.CLEANUP_INTERVAL_MINUTES * 60,
            "options": {"expires": settings.CLEANUP_INTERVAL_MINUTES * 60},
        },
        "purge-expired-refresh-tokens": {
            "task": "app.workers.tasks.purge_expired_refresh_tokens",
            "schedule": 24 * 60 * 60,
        },
    },
)
