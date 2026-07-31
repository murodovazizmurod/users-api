"""Domain-level exceptions.

Services raise these instead of ``HTTPException`` so the business layer stays
framework agnostic and can be reused from Celery tasks or a future gRPC/CLI
entry point. A single handler registered in ``app.main`` maps them to HTTP
responses.
"""

from typing import Any


class AppError(Exception):
    """Base class for expected, client-facing errors."""

    status_code: int = 400
    error_code: str = "bad_request"
    message: str = "Bad request"

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class ValidationError(AppError):
    status_code = 422
    error_code = "validation_error"
    message = "The request payload is invalid"


class AuthenticationError(AppError):
    status_code = 401
    error_code = "authentication_failed"
    message = "Could not validate credentials"


class InvalidCredentialsError(AuthenticationError):
    error_code = "invalid_credentials"
    message = "Incorrect email or password"


class InvalidTokenError(AuthenticationError):
    error_code = "invalid_token"
    message = "Token is invalid or has expired"


class PermissionDeniedError(AppError):
    status_code = 403
    error_code = "permission_denied"
    message = "You do not have permission to perform this action"


class EmailNotVerifiedError(AppError):
    status_code = 403
    error_code = "email_not_verified"
    message = "Account is not verified"


class InactiveUserError(AppError):
    status_code = 403
    error_code = "inactive_user"
    message = "This account has been deactivated"


class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"
    message = "Resource not found"


class UserNotFoundError(NotFoundError):
    error_code = "user_not_found"
    message = "User not found"


class ConflictError(AppError):
    status_code = 409
    error_code = "conflict"
    message = "Resource conflict"


class EmailAlreadyRegisteredError(ConflictError):
    error_code = "email_already_registered"
    message = "A user with this email already exists"


class PhoneAlreadyRegisteredError(ConflictError):
    error_code = "phone_already_registered"
    message = "A user with this phone number already exists"


class AlreadyVerifiedError(ConflictError):
    error_code = "already_verified"
    message = "This account is already verified"


class VerificationFailedError(AppError):
    status_code = 400
    error_code = "verification_failed"
    message = "Verification code is invalid or has expired"


class TooManyAttemptsError(AppError):
    status_code = 429
    error_code = "too_many_attempts"
    message = "Too many attempts, please request a new code"
