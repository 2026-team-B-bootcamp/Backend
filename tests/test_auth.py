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


async def test_logout_revokes_existing_token(client: AsyncClient):
    # 가입 → 발급 토큰으로 보호 엔드포인트 접근 가능
    signup = await client.post(
        "/auth/signup",
        json={"email": "rev@test.com", "password": "pass1234", "display_name": "Rev"},
    )
    token = signup.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/users/me", headers=headers)).status_code == 200

    # 로그아웃 → 서버측 token_version 증가로 이 토큰 무효화
    assert (await client.post("/auth/logout", headers=headers)).status_code == 204

    # 같은(옛) 토큰은 이제 만료 전이라도 거부된다
    assert (await client.get("/users/me", headers=headers)).status_code == 401

    # 재로그인하면 버전이 오른 새 토큰으로 다시 접근 가능
    login = await client.post(
        "/auth/login", json={"email": "rev@test.com", "password": "pass1234"}
    )
    new_token = login.json()["access_token"]
    assert new_token != token
    ok = await client.get("/users/me", headers={"Authorization": f"Bearer {new_token}"})
    assert ok.status_code == 200
