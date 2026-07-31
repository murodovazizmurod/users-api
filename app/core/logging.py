"""Logging configuration.

A single ``configure_logging`` call keeps application, uvicorn and Celery logs
on the same format.
"""

import logging
import sys

from app.core.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging() -> None:
    """Configure root logging once, at process start."""
    root = logging.getLogger()
    if root.handlers:  # already configured (e.g. by the Celery worker)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL.upper())

    # uvicorn installs its own handlers; let records propagate to ours instead.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
