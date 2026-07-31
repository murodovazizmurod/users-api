"""HTTP routes for user management.

Two routers are exported:

* ``me_router``  — the ``/me`` self-service endpoints;
* ``router``     — the ``/users`` collection, mostly administrator-only.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AdminUser, CurrentUser, UserServiceDep
from app.api.responses import error_responses
from app.core.pagination import Page, PaginationParams, pagination_params
from app.modules.auth.schemas import MessageResponse
from app.modules.users.models import UserRole
from app.modules.users.repository import UserFilters
from app.modules.users.schemas import (
    PasswordChange,
    UserAdminUpdate,
    UserCreateByAdmin,
    UserRead,
    UserUpdate,
)

me_router = APIRouter(tags=["Profile"])
router = APIRouter(prefix="/users", tags=["Users"])

PaginationDep = Annotated[PaginationParams, Depends(pagination_params)]


# ---------------------------------------------------------------------------
# Self-service
# ---------------------------------------------------------------------------
@me_router.get(
    "/me",
    response_model=UserRead,
    summary="Get the current user",
    description="Returns the profile of the account the access token belongs to.",
    responses=error_responses(401),
)
async def read_current_user(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)


@me_router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the current user",
    description=(
        "Partially updates the caller's own profile. Only the fields present in the "
        "request body are changed. Privileged fields (`email`, `role`, `is_active`, "
        "`is_verified`) are reserved for administrators."
    ),
    responses=error_responses(401, 403, 422),
)
async def update_current_user(
    payload: UserUpdate, current_user: CurrentUser, user_service: UserServiceDep
) -> UserRead:
    user = await user_service.update_user(
        actor=current_user,
        user_id=current_user.id,
        payload=UserAdminUpdate.model_validate(payload.model_dump(exclude_unset=True)),
    )
    return UserRead.model_validate(user)


@me_router.post(
    "/me/password",
    response_model=MessageResponse,
    summary="Change the current user's password",
    description=(
        "Replaces the caller's password after re-checking the current one. Existing "
        "sessions are left untouched; call `/auth/logout/all` to revoke them."
    ),
    responses=error_responses(401, 422),
)
async def change_password(
    payload: PasswordChange, current_user: CurrentUser, user_service: UserServiceDep
) -> MessageResponse:
    await user_service.change_password(
        user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return MessageResponse(message="Password updated.")


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=Page[UserRead],
    summary="List users",
    description=(
        "Returns a paginated list of users, newest first. **Admin only.** Supports "
        "filtering by role, verification and activity status, and a case-insensitive "
        "search across email, first name and last name."
    ),
    responses=error_responses(401, 403),
)
async def list_users(
    _admin: AdminUser,
    user_service: UserServiceDep,
    pagination: PaginationDep,
    search: Annotated[
        str | None, Query(description="Case-insensitive match on email or name")
    ] = None,
    role: Annotated[UserRole | None, Query(description="Filter by role")] = None,
    is_verified: Annotated[bool | None, Query(description="Filter by verification")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by activity status")] = None,
) -> Page[UserRead]:
    filters = UserFilters(search=search, role=role, is_verified=is_verified, is_active=is_active)
    return await user_service.list_users(filters, pagination)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    description=(
        "Creates a user with an explicit role, optionally already verified. "
        "**Admin only.** Self-service registration goes through `/auth/signup`."
    ),
    responses=error_responses(401, 403, 409, 422),
)
async def create_user(
    payload: UserCreateByAdmin, _admin: AdminUser, user_service: UserServiceDep
) -> UserRead:
    user = await user_service.create_user(
        payload, role=payload.role, is_verified=payload.is_verified
    )
    return UserRead.model_validate(user)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Get a user by ID",
    description="Returns a single user by identifier. **Admin only.**",
    responses=error_responses(401, 403, 404),
)
async def get_user(
    user_id: uuid.UUID, _admin: AdminUser, user_service: UserServiceDep
) -> UserRead:
    user = await user_service.get_user(user_id)
    return UserRead.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Update a user",
    description=(
        "Partially updates a user. Regular users may only update their own profile and "
        "cannot change `email`, `role`, `is_active` or `is_verified`; administrators "
        "may change any field on any account."
    ),
    responses=error_responses(401, 403, 404, 409, 422),
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserAdminUpdate,
    current_user: CurrentUser,
    user_service: UserServiceDep,
) -> UserRead:
    user = await user_service.update_user(actor=current_user, user_id=user_id, payload=payload)
    return UserRead.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user",
    description=(
        "Permanently deletes a user together with their verification codes and "
        "sessions. **Admin only**, and administrators cannot delete their own account."
    ),
    responses=error_responses(401, 403, 404, 409),
)
async def delete_user(
    user_id: uuid.UUID, current_user: CurrentUser, user_service: UserServiceDep
) -> None:
    await user_service.delete_user(actor=current_user, user_id=user_id)
