"""Pydantic schemas for the users module."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.users.models import UserRole

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def validate_password_strength(value: str) -> str:
    """Reject passwords that are trivially guessable.

    Deliberately simple: length plus a letter/digit mix. A production system
    would additionally screen against a breached-password list (e.g. the
    Have I Been Pwned k-anonymity API) and a common-password dictionary.
    """
    if not any(char.isalpha() for char in value):
        raise ValueError("Password must contain at least one letter")
    if not any(char.isdigit() for char in value):
        raise ValueError("Password must contain at least one digit")
    return value


class PasswordMixin(BaseModel):
    """Shared password field with strength validation."""

    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="At least 8 characters, containing letters and digits",
        examples=["Str0ngPassw0rd"],
    )

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return validate_password_strength(value)


class UserBase(BaseModel):
    """Fields shared by user input schemas."""

    first_name: str | None = Field(default=None, max_length=100, examples=["Ada"])
    last_name: str | None = Field(default=None, max_length=100, examples=["Lovelace"])
    phone: str | None = Field(
        default=None,
        max_length=32,
        description="E.164 phone number, required for SMS verification",
        examples=["+14155552671"],
    )

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip_names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().replace(" ", "")
        if not _PHONE_RE.match(value):
            raise ValueError("Phone must be a valid E.164 number, e.g. +14155552671")
        return value


class UserCreate(UserBase, PasswordMixin):
    """Payload for self-service registration."""

    email: EmailStr = Field(description="Unique email address", examples=["ada@example.com"])


class UserCreateByAdmin(UserCreate):
    """Payload for administrative user creation."""

    role: UserRole = Field(default=UserRole.USER, description="Role granted to the new user")
    is_verified: bool = Field(
        default=False, description="Create the account already verified, skipping the code flow"
    )


class UserUpdate(UserBase):
    """Fields a user may change on their own profile.

    Every field is optional: ``PATCH`` applies only the keys present in the
    request body.
    """

    model_config = ConfigDict(extra="forbid")


class UserAdminUpdate(UserUpdate):
    """Additional fields only an administrator may change."""

    email: EmailStr | None = Field(default=None, description="New unique email address")
    role: UserRole | None = Field(default=None, description="Change the user's role")
    is_active: bool | None = Field(default=None, description="Suspend or reactivate the account")
    is_verified: bool | None = Field(default=None, description="Force the verification status")


class UserRead(BaseModel):
    """Public representation of a user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None
    full_name: str | None = Field(description="First and last name joined, when available")
    phone: str | None
    role: UserRole
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    verified_at: datetime | None
    last_login_at: datetime | None


class PasswordChange(BaseModel):
    """Payload for changing one's own password."""

    current_password: str = Field(description="The password currently in use")
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="At least 8 characters, containing letters and digits",
    )

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return validate_password_strength(value)
