"""Pydantic schemas for the auth module."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.modules.users.schemas import UserRead


class TokenPair(BaseModel):
    """A newly issued access/refresh token pair."""

    access_token: str = Field(description="Short-lived JWT for the Authorization header")
    refresh_token: str = Field(description="Long-lived JWT used to obtain a new access token")
    token_type: str = Field(default="bearer", description="Always 'bearer'")
    expires_in: int = Field(description="Access token lifetime in seconds")


class LoginRequest(BaseModel):
    """Credentials submitted to obtain tokens."""

    email: EmailStr = Field(examples=["ada@example.com"])
    password: str = Field(examples=["Str0ngPassw0rd"])


class RefreshRequest(BaseModel):
    """A refresh token exchanged for a new pair."""

    refresh_token: str = Field(description="The refresh token returned by /auth/login")


class LogoutRequest(RefreshRequest):
    """The session to terminate."""


class VerifyRequest(BaseModel):
    """Confirmation of a one-time verification code."""

    email: EmailStr = Field(description="Email the account was registered with")
    code: str = Field(min_length=4, max_length=10, examples=["123456"])


class ResendVerificationRequest(BaseModel):
    """Request for a fresh verification code."""

    email: EmailStr = Field(description="Email the account was registered with")


class SignupResponse(BaseModel):
    """Result of a registration request."""

    user: UserRead
    verification_required: bool = Field(
        default=True, description="Whether a verification code must still be confirmed"
    )
    message: str = Field(description="Human-readable next step")


class MessageResponse(BaseModel):
    """Generic acknowledgement."""

    message: str


class ErrorResponse(BaseModel):
    """Uniform error body returned by every failing endpoint."""

    error: str = Field(description="Machine-readable error code, e.g. 'invalid_credentials'")
    message: str = Field(description="Human-readable explanation")
    details: dict = Field(default_factory=dict, description="Optional structured context")
