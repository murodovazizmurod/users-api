"""Notification transport abstraction.

The verification flow depends on this protocol rather than on a concrete
provider, so swapping the console stub for SMTP, SendGrid or Twilio is a
configuration change instead of a code change.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Sends a one-time verification code to a destination."""

    async def send_verification_code(
        self, *, destination: str, code: str, link: str, expires_in_minutes: int
    ) -> None:
        """Deliver ``code`` to ``destination``.

        Args:
            destination: Email address or phone number.
            code: The plaintext one-time code.
            link: A ready-made confirmation link containing the code.
            expires_in_minutes: Lifetime of the code, for the message body.
        """
        ...
