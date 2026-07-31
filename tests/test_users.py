"""Profile and user-administration endpoints, including role enforcement."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.modules.users.models import User


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/me")
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


async def test_me_rejects_a_malformed_token(client: AsyncClient) -> None:
    response = await client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_me_returns_the_current_user(
    client: AsyncClient, user_headers: dict[str, str], regular_user: User
) -> None:
    response = await client.get("/me", headers=user_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == regular_user.email
    assert body["id"] == str(regular_user.id)
    assert "hashed_password" not in body


async def test_user_can_update_own_profile(
    client: AsyncClient, user_headers: dict[str, str]
) -> None:
    response = await client.patch(
        "/me", headers=user_headers, json={"first_name": "Grace", "last_name": "Hopper"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Grace Hopper"


async def test_user_cannot_escalate_own_role(
    client: AsyncClient, user_headers: dict[str, str], regular_user: User
) -> None:
    response = await client.patch(
        f"/users/{regular_user.id}", headers=user_headers, json={"role": "admin"}
    )

    assert response.status_code == 403
    assert response.json()["error"] == "permission_denied"


async def test_user_cannot_update_another_account(
    client: AsyncClient, user_headers: dict[str, str], admin_user: User
) -> None:
    response = await client.patch(
        f"/users/{admin_user.id}", headers=user_headers, json={"first_name": "Nope"}
    )
    assert response.status_code == 403


async def test_listing_users_is_admin_only(
    client: AsyncClient, user_headers: dict[str, str]
) -> None:
    response = await client.get("/users", headers=user_headers)
    assert response.status_code == 403


async def test_admin_can_list_and_filter_users(
    client: AsyncClient, admin_headers: dict[str, str], regular_user: User
) -> None:
    response = await client.get("/users", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert {item["email"] for item in body["items"]} == {"admin@example.com", "user@example.com"}

    filtered = await client.get("/users", params={"role": "admin"}, headers=admin_headers)
    assert filtered.json()["total"] == 1

    searched = await client.get("/users", params={"search": "USER@"}, headers=admin_headers)
    assert searched.json()["total"] == 1


async def test_admin_pagination(
    client: AsyncClient, admin_headers: dict[str, str], regular_user: User
) -> None:
    page = await client.get("/users", params={"limit": 1, "offset": 0}, headers=admin_headers)
    body = page.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["limit"] == 1


async def test_admin_can_read_a_user_by_id(
    client: AsyncClient, admin_headers: dict[str, str], regular_user: User
) -> None:
    response = await client.get(f"/users/{regular_user.id}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["email"] == regular_user.email


async def test_reading_a_missing_user_returns_404(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.get(f"/users/{uuid.uuid4()}", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["error"] == "user_not_found"


async def test_admin_can_change_role_and_verification(
    client: AsyncClient, admin_headers: dict[str, str], regular_user: User
) -> None:
    response = await client.patch(
        f"/users/{regular_user.id}", headers=admin_headers, json={"role": "admin"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "admin"


async def test_admin_cannot_demote_themselves(
    client: AsyncClient, admin_headers: dict[str, str], admin_user: User
) -> None:
    response = await client.patch(
        f"/users/{admin_user.id}", headers=admin_headers, json={"role": "user"}
    )
    assert response.status_code == 409


async def test_admin_can_create_a_verified_user(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/users",
        headers=admin_headers,
        json={
            "email": "new@example.com",
            "password": "Str0ngPassw0rd",
            "role": "admin",
            "is_verified": True,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "admin"
    assert body["is_verified"] is True


async def test_deleting_a_user_is_admin_only(
    client: AsyncClient, user_headers: dict[str, str], admin_user: User
) -> None:
    response = await client.delete(f"/users/{admin_user.id}", headers=user_headers)
    assert response.status_code == 403


async def test_admin_can_delete_a_user(
    client: AsyncClient, admin_headers: dict[str, str], regular_user: User
) -> None:
    response = await client.delete(f"/users/{regular_user.id}", headers=admin_headers)
    assert response.status_code == 204

    missing = await client.get(f"/users/{regular_user.id}", headers=admin_headers)
    assert missing.status_code == 404


async def test_admin_cannot_delete_themselves(
    client: AsyncClient, admin_headers: dict[str, str], admin_user: User
) -> None:
    response = await client.delete(f"/users/{admin_user.id}", headers=admin_headers)
    assert response.status_code == 409


async def test_password_change_requires_the_current_password(
    client: AsyncClient, user_headers: dict[str, str]
) -> None:
    wrong = await client.post(
        "/me/password",
        headers=user_headers,
        json={"current_password": "Nope12345", "new_password": "BrandNewPass1"},
    )
    assert wrong.status_code == 401

    correct = await client.post(
        "/me/password",
        headers=user_headers,
        json={"current_password": "UserPass123", "new_password": "BrandNewPass1"},
    )
    assert correct.status_code == 200

    relogin = await client.post(
        "/auth/login", json={"email": "user@example.com", "password": "BrandNewPass1"}
    )
    assert relogin.status_code == 200
