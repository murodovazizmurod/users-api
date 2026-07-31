"""Notification transports."""

from functools import lru_cache

from app.notifications.base import Notifier
from app.notifications.console import ConsoleNotifier

__all__ = ["Notifier", "ConsoleNotifier", "get_notifier"]


@lru_cache
def get_notifier() -> Notifier:
    """Return the notifier for the configured channel.

    Only the console implementation exists today; see
    :mod:`app.notifications.console` for how a real provider would be added.
    """
    return ConsoleNotifier()
