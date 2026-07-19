from httpx import AsyncClient


async def test_signup_returns_token(client: AsyncClient):
    resp = await client.post(
        "/auth/signup",
        json={"email": "a@test.com", "password": "pass1234", "display_name": "Alice"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_after_signup(client: AsyncClient):
    await client.post(
        "/auth/signup",
        json={"email": "a@test.com", "password": "pass1234", "display_name": "Alice"},
    )
    resp = await client.post(
        "/auth/login", json={"email": "a@test.com", "password": "pass1234"}
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_login_wrong_password_401(client: AsyncClient):
    await client.post(
        "/auth/signup",
        json={"email": "a@test.com", "password": "pass1234", "display_name": "Alice"},
    )
    resp = await client.post(
        "/auth/login", json={"email": "a@test.com", "password": "wrongpass"}
    )
    assert resp.status_code == 401


async def test_duplicate_email_409(client: AsyncClient):
    payload = {"email": "a@test.com", "password": "pass1234", "display_name": "Alice"}
    await client.post("/auth/signup", json=payload)
    resp = await client.post("/auth/signup", json=payload)
    assert resp.status_code == 409
