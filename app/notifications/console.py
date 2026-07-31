"""Development notifier that prints codes to the application log.

SIMPLIFICATION: the task allows a fake delivery channel in development, so no
real provider is wired up. In production I would keep this exact interface and
add:

* ``SmtpNotifier`` / ``SendGridNotifier`` for email and ``TwilioNotifier`` for
  SMS, chosen by ``VERIFICATION_CHANNEL`` in :func:`get_notifier`;
* delivery handed to Celery (``send_verification_code.delay(...)``) so a slow
  or failing provider never blocks the signup request, with retries and
  exponential backoff on the task;
* templated messages (Jinja2) with a plaintext and an HTML part;
* provider webhooks for bounces/complaints feeding a suppression list.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ConsoleNotifier:
    """Logs the verification code instead of sending it."""

    async def send_verification_code(
        self, *, destination: str, code: str, link: str, expires_in_minutes: int
    ) -> None:
        logger.info(
            "\n"
            "==================== VERIFICATION CODE ====================\n"
            "  To:      %s\n"
            "  Code:    %s\n"
            "  Link:    %s\n"
            "  Expires: in %s minutes\n"
            "===========================================================",
            destination,
            code,
            link,
            expires_in_minutes,
        )
