from httpx import AsyncClient


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_creator_auto_joined_with_default_channel(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    resp = await client.post("/servers", json={"name": "Team"}, headers=_headers(token))
    assert resp.status_code == 200
    server = resp.json()
    assert server["invite_code"]

    listed = await client.get("/servers", headers=_headers(token))
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()] == [server["id"]]

    channels = await client.get(
        f"/servers/{server['id']}/channels", headers=_headers(token)
    )
    assert channels.status_code == 200
    names = [c["name"] for c in channels.json()]
    assert names == ["일반"]


async def test_join_by_invite_code(client: AsyncClient, register):
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token_a))
    ).json()

    resp = await client.post(
        "/servers/join",
        json={"invite_code": server["invite_code"]},
        headers=_headers(token_b),
    )
    assert resp.status_code == 200

    members = await client.get(
        f"/servers/{server['id']}/members", headers=_headers(token_a)
    )
    assert members.status_code == 200
    names = {m["display_name"] for m in members.json()}
    assert names == {"Alice", "Bob"}


async def test_join_invalid_code_404(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    resp = await client.post(
        "/servers/join", json={"invite_code": "NOPE1234"}, headers=_headers(token)
    )
    assert resp.status_code == 404


async def test_create_extra_channel(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token))
    ).json()

    created = await client.post(
        f"/servers/{server['id']}/channels",
        json={"name": "잡담"},
        headers=_headers(token),
    )
    assert created.status_code == 200
    assert created.json()["name"] == "잡담"

    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(token))
    ).json()
    assert [c["name"] for c in channels] == ["일반", "잡담"]


async def test_non_member_cannot_view_members_403(client: AsyncClient, register):
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token_a))
    ).json()

    resp = await client.get(
        f"/servers/{server['id']}/members", headers=_headers(token_b)
    )
    assert resp.status_code == 403

    channels = await client.get(
        f"/servers/{server['id']}/channels", headers=_headers(token_b)
    )
    assert channels.status_code == 403


async def test_members_requires_auth(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token))
    ).json()
    resp = await client.get(f"/servers/{server['id']}/members")
    assert resp.status_code == 401
