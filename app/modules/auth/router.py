"""HTTP routes for registration, authentication and verification."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AuthServiceDep, ClientAddress, CurrentUser, RateLimiterDep
from app.api.responses import error_responses
from app.core.exceptions import AuthenticationError
from app.core.rate_limit import enforce, login_rule, resend_rule, signup_rule
from app.modules.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    ResendVerificationRequest,
    SignupResponse,
    TokenPair,
    VerifyRequest,
)
from app.modules.users.schemas import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description=(
        "Creates an account with the given email and password. The email must not "
        "already be registered. The account starts in the **unverified** state and a "
        "one-time verification code is sent through the configured channel — in "
        "development it is written to the application log. Unverified accounts are "
        "deleted automatically after the retention period.\n\n"
        "Rate limited per client address."
    ),
    responses=error_responses(409, 422, 429),
)
async def signup(
    payload: UserCreate,
    auth_service: AuthServiceDep,
    limiter: RateLimiterDep,
    client: ClientAddress,
) -> SignupResponse:
    rule = signup_rule()
    key = f"ratelimit:signup:{client}"
    await enforce(limiter, key, rule, "Too many registrations from this address")
    await limiter.hit(key, rule)

    user = await auth_service.signup(payload)
    return SignupResponse(
        user=UserRead.model_validate(user),
        verification_required=True,
        message="Account created. Confirm the code sent to you via POST /auth/verify.",
    )


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Authenticate and obtain tokens",
    description=(
        "Exchanges email and password for a short-lived access token and a long-lived "
        "refresh token. Unverified users can sign in, but endpoints that require a "
        "confirmed account will reject their access token with 403.\n\n"
        "**Failed** attempts are rate limited per client address and email; a "
        "successful sign-in clears the counter, so a legitimate user is never locked "
        "out by their own typos. Exceeding the budget returns 429 with `Retry-After`."
    ),
    responses=error_responses(401, 403, 422, 429),
)
async def login(
    payload: LoginRequest,
    auth_service: AuthServiceDep,
    limiter: RateLimiterDep,
    client: ClientAddress,
) -> TokenPair:
    rule = login_rule()
    key = f"ratelimit:login:{client}:{payload.email.strip().lower()}"
    await enforce(limiter, key, rule, "Too many failed sign-in attempts")

    try:
        user = await auth_service.authenticate(payload.email, payload.password)
    except AuthenticationError:
        # Only wrong credentials consume the budget.
        await limiter.hit(key, rule)
        raise

    await limiter.reset(key)
    return await auth_service.issue_token_pair(user)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Refresh the access token",
    description=(
        "Exchanges a valid refresh token for a new access/refresh pair. Refresh tokens "
        "are single-use: the presented token is revoked and replaced. Re-using an "
        "already-rotated token is treated as a compromise and revokes every session of "
        "that user."
    ),
    responses=error_responses(401, 403, 422),
)
async def refresh(payload: RefreshRequest, auth_service: AuthServiceDep) -> TokenPair:
    return await auth_service.refresh_tokens(payload.refresh_token)


@router.post(
    "/verify",
    response_model=UserRead,
    summary="Confirm a verification code",
    description=(
        "Confirms the one-time code sent after registration and marks the account as "
        "**verified**. Codes are single-use, expire after a configurable period and "
        "are invalidated after too many failed attempts."
    ),
    responses=error_responses(400, 409, 422, 429),
)
async def verify(payload: VerifyRequest, auth_service: AuthServiceDep) -> UserRead:
    user = await auth_service.verify(payload.email, payload.code)
    return UserRead.model_validate(user)


@router.post(
    "/verify/resend",
    response_model=MessageResponse,
    summary="Request a new verification code",
    description=(
        "Issues a fresh code and invalidates any previous one. The response is "
        "identical whether or not the address is registered, so the endpoint cannot be "
        "used to enumerate accounts.\n\n"
        "Rate limited per email address, since every accepted call sends a message."
    ),
    responses=error_responses(422, 429),
)
async def resend_verification(
    payload: ResendVerificationRequest,
    auth_service: AuthServiceDep,
    limiter: RateLimiterDep,
) -> MessageResponse:
    rule = resend_rule()
    key = f"ratelimit:resend:{payload.email.strip().lower()}"
    await enforce(limiter, key, rule, "Too many verification codes requested for this address")
    await limiter.hit(key, rule)

    await auth_service.resend_verification_code(payload.email)
    return MessageResponse(
        message="If the address belongs to an unverified account, a new code has been sent."
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Terminate the current session",
    description=(
        "Revokes the supplied refresh token. The matching access token stays valid "
        "until it expires, which is a few minutes by default."
    ),
    responses=error_responses(401, 422),
)
async def logout(payload: LogoutRequest, auth_service: AuthServiceDep) -> MessageResponse:
    await auth_service.logout(payload.refresh_token)
    return MessageResponse(message="Session terminated.")


@router.post(
    "/logout/all",
    response_model=MessageResponse,
    summary="Terminate every session of the current user",
    description="Revokes all refresh tokens issued to the authenticated user.",
    responses=error_responses(401),
)
async def logout_all(current_user: CurrentUser, auth_service: AuthServiceDep) -> MessageResponse:
    revoked = await auth_service.logout_everywhere(current_user)
    return MessageResponse(message=f"Revoked {revoked} session(s).")
