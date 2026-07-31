"""Password hashing, one-time codes and JWT encoding/decoding.

Kept free of database and FastAPI imports so it can be unit tested and reused
by any layer.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings
from app.core.exceptions import InvalidTokenError

TokenType = Literal["access", "refresh"]

# Argon2id is the current password-hashing recommendation and, unlike bcrypt,
# has no 72-byte input limit, so long passphrases are not silently truncated.
_password_hasher = PasswordHasher()


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored hash."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Report whether a stored hash uses outdated Argon2 parameters.

    Callers re-hash on the next successful login so parameters can be raised
    over time without a forced password reset.
    """
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# --------------------------------------------------------------------------
# One-time verification codes
# --------------------------------------------------------------------------
def generate_numeric_code(length: int) -> str:
    """Generate a cryptographically secure numeric code of a fixed length."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_code(code: str) -> str:
    """Hash a one-time code before storing it.

    Verification codes are bearer secrets: a database leak must not let an
    attacker verify somebody else's account. They are short-lived and
    high-entropy-limited, so a fast keyed digest is enough here — Argon2 would
    add latency without a meaningful security gain against a keyed HMAC.
    """
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def verify_code(code: str, code_hash: str) -> bool:
    """Constant-time comparison of a submitted code against its stored hash."""
    return hmac.compare_digest(hash_code(code), code_hash)


# --------------------------------------------------------------------------
# JWT
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TokenPayload:
    """Decoded JWT claims used by the application."""

    subject: uuid.UUID
    token_type: TokenType
    jti: uuid.UUID
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly minted token plus the metadata needed to persist it."""

    token: str
    jti: uuid.UUID
    expires_at: datetime


def _create_token(subject: uuid.UUID, token_type: TokenType, ttl: timedelta) -> IssuedToken:
    now = datetime.now(UTC)
    expires_at = now + ttl
    jti = uuid.uuid4()
    claims: dict[str, Any] = {
        "sub": str(subject),
        "typ": token_type,
        "jti": str(jti),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.JWT_ISSUER,
    }
    token = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return IssuedToken(token=token, jti=jti, expires_at=expires_at)


def create_access_token(subject: uuid.UUID) -> IssuedToken:
    """Issue a short-lived access token."""
    return _create_token(subject, "access", timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES))


def create_refresh_token(subject: uuid.UUID) -> IssuedToken:
    """Issue a long-lived refresh token."""
    return _create_token(subject, "refresh", timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS))


def decode_token(token: str, expected_type: TokenType) -> TokenPayload:
    """Decode and validate a JWT, enforcing its type.

    Raises:
        InvalidTokenError: the signature, issuer, expiry or type is not valid.
    """
    try:
        claims = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:  # noqa: BLE001 - all JWT failures are one error to the client
        raise InvalidTokenError() from exc

    if claims.get("typ") != expected_type:
        raise InvalidTokenError(f"Expected a {expected_type} token")

    try:
        return TokenPayload(
            subject=uuid.UUID(claims["sub"]),
            token_type=expected_type,
            jti=uuid.UUID(claims["jti"]),
            issued_at=datetime.fromtimestamp(claims["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
        )
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError() from exc
