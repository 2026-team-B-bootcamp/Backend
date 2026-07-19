from httpx import AsyncClient


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_server(client: AsyncClient, token: str) -> dict:
    return (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token))
    ).json()


async def test_upsert_reflected_in_members(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    server = await _create_server(client, token)

    resp = await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["축구", "영화", "커피"]

    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token))
    ).json()
    me = members[0]
    assert me["tags"] == ["축구", "영화", "커피"]


async def test_upsert_overwrites(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    server = await _create_server(client, token)
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token),
    )
    resp = await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "농구", "tag2": "게임", "tag3": "독서"},
        headers=_headers(token),
    )
    assert resp.json()["tags"] == ["농구", "게임", "독서"]


async def test_common_tags_intersection(client: AsyncClient, register):
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = await _create_server(client, token_a)
    await client.post(
        "/servers/join",
        json={"invite_code": server["invite_code"]},
        headers=_headers(token_b),
    )

    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token_a),
    )
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "게임", "tag3": "독서"},
        headers=_headers(token_b),
    )

    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    bob = next(m for m in members if m["display_name"] == "Bob")
    assert bob["common_with_me"] == ["축구"]
    alice = next(m for m in members if m["display_name"] == "Alice")
    assert alice["common_with_me"] == []


async def test_non_member_cannot_set_tags_403(client: AsyncClient, register):
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = await _create_server(client, token_a)
    resp = await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "x", "tag2": "y", "tag3": "z"},
        headers=_headers(token_b),
    )
    assert resp.status_code == 403
