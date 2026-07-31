"""Registration, login, refresh and verification flows."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import RecordingNotifier, auth_header

SIGNUP_PAYLOAD = {
    "email": "ada@example.com",
    "password": "Str0ngPassw0rd",
    "first_name": "Ada",
    "last_name": "Lovelace",
}


async def test_health_endpoint(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_signup_creates_unverified_user(
    client: AsyncClient, notifier: RecordingNotifier
) -> None:
    response = await client.post("/auth/signup", json=SIGNUP_PAYLOAD)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["is_verified"] is False
    assert body["user"]["role"] == "user"
    assert body["user"]["full_name"] == "Ada Lovelace"
    assert body["verification_required"] is True
    # A code was handed to the notifier rather than returned in the response.
    assert notifier.last_code_for("ada@example.com")
    assert "password" not in response.text


async def test_signup_normalises_email_and_rejects_duplicates(client: AsyncClient) -> None:
    first = await client.post("/auth/signup", json=SIGNUP_PAYLOAD)
    assert first.status_code == 201

    duplicate = await client.post(
        "/auth/signup", json={**SIGNUP_PAYLOAD, "email": "ADA@Example.com"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "email_already_registered"


async def test_signup_rejects_weak_password(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/signup", json={**SIGNUP_PAYLOAD, "password": "password"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


async def test_full_verification_flow(client: AsyncClient, notifier: RecordingNotifier) -> None:
    await client.post("/auth/signup", json=SIGNUP_PAYLOAD)
    code = notifier.last_code_for("ada@example.com")

    response = await client.post(
        "/auth/verify", json={"email": "ada@example.com", "code": code}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_verified"] is True
    assert body["verified_at"] is not None


async def test_verify_rejects_wrong_code(client: AsyncClient, notifier: RecordingNotifier) -> None:
    await client.post("/auth/signup", json=SIGNUP_PAYLOAD)

    response = await client.post(
        "/auth/verify", json={"email": "ada@example.com", "code": "000000"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "verification_failed"


async def test_verification_code_is_single_use(
    client: AsyncClient, notifier: RecordingNotifier
) -> None:
    await client.post("/auth/signup", json=SIGNUP_PAYLOAD)
    code = notifier.last_code_for("ada@example.com")

    assert (
        await client.post("/auth/verify", json={"email": "ada@example.com", "code": code})
    ).status_code == 200

    replay = await client.post("/auth/verify", json={"email": "ada@example.com", "code": code})
    assert replay.status_code == 409
    assert replay.json()["error"] == "already_verified"


async def test_resending_a_code_invalidates_the_previous_one(
    client: AsyncClient, notifier: RecordingNotifier
) -> None:
    await client.post("/auth/signup", json=SIGNUP_PAYLOAD)
    first_code = notifier.last_code_for("ada@example.com")

    resend = await client.post("/auth/verify/resend", json={"email": "ada@example.com"})
    assert resend.status_code == 200
    second_code = notifier.last_code_for("ada@example.com")
    assert second_code != first_code

    stale = await client.post(
        "/auth/verify", json={"email": "ada@example.com", "code": first_code}
    )
    assert stale.status_code == 400

    fresh = await client.post(
        "/auth/verify", json={"email": "ada@example.com", "code": second_code}
    )
    assert fresh.status_code == 200


async def test_resend_does_not_disclose_unknown_accounts(client: AsyncClient) -> None:
    response = await client.post("/auth/verify/resend", json={"email": "nobody@example.com"})
    assert response.status_code == 200


async def test_login_returns_token_pair(client: AsyncClient, regular_user) -> None:  # noqa: ANN001
    response = await client.post(
        "/auth/login", json={"email": "user@example.com", "password": "UserPass123"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


async def test_login_rejects_bad_credentials(client: AsyncClient, regular_user) -> None:  # noqa: ANN001
    response = await client.post(
        "/auth/login", json={"email": "user@example.com", "password": "WrongPass123"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_credentials"


async def test_login_of_unknown_email_looks_identical(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "ghost@example.com", "password": "WhateverPass1"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_credentials"


async def test_refresh_rotates_the_token(client: AsyncClient, regular_user) -> None:  # noqa: ANN001
    tokens = (
        await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "UserPass123"}
        )
    ).json()

    refreshed = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert refreshed.status_code == 200, refreshed.text
    new_tokens = refreshed.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # The new access token works.
    me = await client.get("/me", headers=auth_header(new_tokens["access_token"]))
    assert me.status_code == 200


async def test_replaying_a_rotated_refresh_token_kills_every_session(
    client: AsyncClient, regular_user  # noqa: ANN001
) -> None:
    tokens = (
        await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "UserPass123"}
        )
    ).json()
    rotated = (
        await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).json()

    replay = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401

    # The token issued by the legitimate refresh is revoked as well.
    after = await client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert after.status_code == 401


async def test_refresh_rejects_an_access_token(client: AsyncClient, regular_user) -> None:  # noqa: ANN001
    tokens = (
        await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "UserPass123"}
        )
    ).json()

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert response.status_code == 401


async def test_logout_revokes_the_session(client: AsyncClient, regular_user) -> None:  # noqa: ANN001
    tokens = (
        await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "UserPass123"}
        )
    ).json()

    logout = await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 200

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401
