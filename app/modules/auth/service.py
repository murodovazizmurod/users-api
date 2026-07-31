"""Authentication, token lifecycle and account verification."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AlreadyVerifiedError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
    TooManyAttemptsError,
    UserNotFoundError,
    VerificationFailedError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_numeric_code,
    hash_code,
    hash_password,
    password_needs_rehash,
    verify_code,
    verify_password,
)
from app.modules.auth.models import RefreshToken, VerificationChannel, VerificationCode
from app.modules.auth.repository import RefreshTokenRepository, VerificationCodeRepository
from app.modules.auth.schemas import TokenPair
from app.modules.users.models import User
from app.modules.users.schemas import UserCreate
from app.modules.users.service import UserService
from app.notifications import Notifier

logger = logging.getLogger(__name__)


class AuthService:
    """Signup, login, token refresh and email/SMS verification."""

    def __init__(self, session: AsyncSession, notifier: Notifier) -> None:
        self.session = session
        self.notifier = notifier
        self.users = UserService(session)
        self.codes = VerificationCodeRepository(session)
        self.tokens = RefreshTokenRepository(session)

    # -- registration ------------------------------------------------------
    async def signup(self, payload: UserCreate) -> User:
        """Register a user and send the first verification code."""
        user = await self.users.create_user(payload)
        await self.issue_verification_code(user)
        return user

    # -- login -------------------------------------------------------------
    async def authenticate(self, email: str, password: str) -> User:
        """Validate credentials and return the matching user.

        Unverified users are allowed to sign in: they need a session to call
        the verification endpoints and to see their own profile. Endpoints
        that require a confirmed account use the ``require_verified_user``
        dependency instead.
        """
        user = await self.users.get_by_email(email)
        if user is None:
            # Hash a dummy value so a missing account and a wrong password take
            # comparable time, leaving no timing oracle for email enumeration.
            hash_password(password)
            raise InvalidCredentialsError()

        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError()

        if not user.is_active:
            raise InactiveUserError()

        if password_needs_rehash(user.hashed_password):
            user.hashed_password = hash_password(password)

        user.last_login_at = datetime.now(UTC)
        await self.session.commit()
        return user

    # -- tokens ------------------------------------------------------------
    async def issue_token_pair(self, user: User) -> TokenPair:
        """Mint an access/refresh pair and record the refresh token."""
        access = create_access_token(user.id)
        refresh = create_refresh_token(user.id)

        self.tokens.add(
            RefreshToken(user_id=user.id, jti=refresh.jti, expires_at=refresh.expires_at)
        )
        await self.session.commit()

        return TokenPair(
            access_token=access.token,
            refresh_token=refresh.token,
            expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        )

    async def refresh_tokens(self, refresh_token: str) -> TokenPair:
        """Exchange a refresh token for a new pair, rotating the old one.

        Rotation plus replay detection: presenting an already-rotated token is
        treated as a stolen-credential signal and kills every session of that
        user.
        """
        payload = decode_token(refresh_token, expected_type="refresh")

        stored = await self.tokens.get_by_jti(payload.jti)
        if stored is None:
            raise InvalidTokenError("Refresh token is not recognised")

        if not stored.is_active:
            revoked = await self.tokens.revoke_all_for_user(stored.user_id)
            await self.session.commit()
            logger.warning(
                "Refresh token replay detected: user_id=%s jti=%s sessions_revoked=%s",
                stored.user_id,
                stored.jti,
                revoked,
            )
            raise InvalidTokenError("Refresh token has already been used or revoked")

        user = await self.users.repository.get_by_id(stored.user_id)
        if user is None:
            raise InvalidTokenError()
        if not user.is_active:
            raise InactiveUserError()

        access = create_access_token(user.id)
        new_refresh = create_refresh_token(user.id)

        stored.revoked_at = datetime.now(UTC)
        stored.replaced_by_jti = new_refresh.jti
        self.tokens.add(
            RefreshToken(user_id=user.id, jti=new_refresh.jti, expires_at=new_refresh.expires_at)
        )
        await self.session.commit()

        return TokenPair(
            access_token=access.token,
            refresh_token=new_refresh.token,
            expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        )

    async def logout(self, refresh_token: str) -> None:
        """Revoke the session behind a refresh token.

        Access tokens stay valid until they expire (minutes). Revoking them
        immediately would need a shared deny-list keyed by ``jti`` in Redis,
        checked on every request — worth adding once there is a Redis
        dependency in the request path.
        """
        payload = decode_token(refresh_token, expected_type="refresh")
        stored = await self.tokens.get_by_jti(payload.jti)
        if stored is not None and stored.revoked_at is None:
            stored.revoked_at = datetime.now(UTC)
            await self.session.commit()

    async def logout_everywhere(self, user: User) -> int:
        """Revoke every refresh token of a user."""
        count = await self.tokens.revoke_all_for_user(user.id)
        await self.session.commit()
        return count

    async def resolve_access_token(self, token: str) -> User:
        """Return the user behind an access token, or raise."""
        payload = decode_token(token, expected_type="access")
        user = await self.users.repository.get_by_id(payload.subject)
        if user is None:
            raise InvalidTokenError("The account for this token no longer exists")
        if not user.is_active:
            raise InactiveUserError()
        return user

    # -- verification ------------------------------------------------------
    async def issue_verification_code(self, user: User) -> None:
        """Generate, store and deliver a fresh verification code."""
        if user.is_verified:
            raise AlreadyVerifiedError()

        channel = VerificationChannel(settings.VERIFICATION_CHANNEL)
        destination = user.phone if channel is VerificationChannel.SMS else user.email
        if not destination:
            raise VerificationFailedError("No phone number on file for SMS verification")

        await self.codes.invalidate_active_for_user(user.id)

        code = generate_numeric_code(settings.VERIFICATION_CODE_LENGTH)
        record = VerificationCode(
            user_id=user.id,
            channel=channel,
            destination=destination,
            code_hash=hash_code(code),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=settings.VERIFICATION_CODE_TTL_MINUTES),
        )
        self.codes.add(record)
        await self.session.commit()

        query = urlencode({"email": user.email, "code": code})
        await self.notifier.send_verification_code(
            destination=destination,
            code=code,
            link=f"{settings.PUBLIC_BASE_URL.rstrip('/')}/verify?{query}",
            expires_in_minutes=settings.VERIFICATION_CODE_TTL_MINUTES,
        )

    async def verify(self, email: str, code: str) -> User:
        """Confirm a verification code and mark the account as verified."""
        user = await self.users.get_by_email(email)
        if user is None:
            raise VerificationFailedError()
        if user.is_verified:
            raise AlreadyVerifiedError()

        record = await self.codes.get_active_for_user(user.id)
        if record is None:
            raise VerificationFailedError("No active verification code, request a new one")

        if record.attempts >= settings.VERIFICATION_MAX_ATTEMPTS:
            record.used_at = datetime.now(UTC)
            await self.session.commit()
            raise TooManyAttemptsError()

        if not verify_code(code, record.code_hash):
            record.attempts += 1
            await self.session.commit()
            raise VerificationFailedError()

        now = datetime.now(UTC)
        record.used_at = now
        user.is_verified = True
        user.verified_at = now
        await self.session.commit()
        await self.session.refresh(user)

        logger.info("User verified: id=%s email=%s", user.id, user.email)
        return user

    async def resend_verification_code(self, email: str) -> None:
        """Re-issue a verification code.

        Always succeeds from the caller's point of view so the endpoint cannot
        be used to discover which addresses are registered.

        SIMPLIFICATION: there is no per-address rate limit here. In production
        this endpoint would sit behind a Redis token bucket (a few requests per
        address and per IP per hour) to keep it from being used to spam
        somebody's inbox.
        """
        user = await self.users.get_by_email(email)
        if user is None or user.is_verified or not user.is_active:
            logger.info("Verification resend ignored for %s", email)
            return
        await self.issue_verification_code(user)

    # -- bootstrap ---------------------------------------------------------
    async def ensure_user_exists(self, user_id: uuid.UUID) -> User:
        user = await self.users.repository.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError()
        return user
