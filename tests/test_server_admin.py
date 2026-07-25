"""서버·채널 이름 변경과 방장의 멤버 내보내기.

권한 경계가 핵심이다 — 이름은 멤버면 누구나, 내보내기는 만든 사람만.
"""

from httpx import AsyncClient


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _team(client: AsyncClient, register):
    """Alice(방장)가 만든 서버에 Bob이 참여한 상태를 만든다."""
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    token_b = await register(client, "b@test.com", "pass1234", "Bob")
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token_a))
    ).json()
    await client.post(
        "/servers/join",
        json={"invite_code": server["invite_code"]},
        headers=_headers(token_b),
    )
    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(token_a))
    ).json()
    return token_a, token_b, server, channels[0]


async def test_create_server_reports_owner(client: AsyncClient, register):
    token = await register(client, "a@test.com", "pass1234", "Alice")
    me = (await client.get("/users/me", headers=_headers(token))).json()
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token))
    ).json()
    assert server["owner_user_id"] == me["id"]


async def test_member_can_rename_server_and_channel(client: AsyncClient, register):
    """이름 변경은 방장 전용이 아니다 — 참여한 멤버(Bob)도 고칠 수 있다."""
    _, token_b, server, channel = await _team(client, register)

    renamed = await client.patch(
        f"/servers/{server['id']}", json={"name": "새 모임"}, headers=_headers(token_b)
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "새 모임"

    renamed_ch = await client.patch(
        f"/servers/{server['id']}/channels/{channel['id']}",
        json={"name": "잡담"},
        headers=_headers(token_b),
    )
    assert renamed_ch.status_code == 200
    assert renamed_ch.json()["name"] == "잡담"

    # 목록에도 반영돼야 한다 (응답만 바꾸고 저장이 빠지는 실수를 잡는다)
    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(token_b))
    ).json()
    assert [c["name"] for c in channels] == ["잡담"]


async def test_non_member_cannot_rename(client: AsyncClient, register):
    token_a, _, server, channel = await _team(client, register)
    outsider = await register(client, "c@test.com", "pass1234", "Carol")

    assert (
        await client.patch(
            f"/servers/{server['id']}", json={"name": "탈취"}, headers=_headers(outsider)
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/servers/{server['id']}/channels/{channel['id']}",
            json={"name": "탈취"},
            headers=_headers(outsider),
        )
    ).status_code == 403


async def test_cannot_rename_channel_of_another_server(client: AsyncClient, register):
    """내가 멤버인 서버 id + 남의 채널 id 조합으로 남의 채널을 고칠 수 없다."""
    token_a, _, mine, _ = await _team(client, register)
    other_owner = await register(client, "c@test.com", "pass1234", "Carol")
    theirs = (
        await client.post("/servers", json={"name": "남의 방"}, headers=_headers(other_owner))
    ).json()
    their_channel = (
        await client.get(f"/servers/{theirs['id']}/channels", headers=_headers(other_owner))
    ).json()[0]

    resp = await client.patch(
        f"/servers/{mine['id']}/channels/{their_channel['id']}",
        json={"name": "탈취"},
        headers=_headers(token_a),
    )
    assert resp.status_code == 404


async def test_owner_kicks_member(client: AsyncClient, register):
    token_a, token_b, server, _ = await _team(client, register)
    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    bob = next(m for m in members if m["display_name"] == "Bob")
    alice = next(m for m in members if m["display_name"] == "Alice")
    assert alice["is_owner"] is True
    assert bob["is_owner"] is False

    resp = await client.delete(
        f"/servers/{server['id']}/members/{bob['user_id']}", headers=_headers(token_a)
    )
    assert resp.status_code == 204

    remaining = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    assert [m["display_name"] for m in remaining] == ["Alice"]
    # 내보낸 사람은 더 이상 이 서버를 볼 수 없다
    assert (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_b))
    ).status_code == 403


async def test_kicked_member_tags_are_removed(client: AsyncClient, register):
    """나간 사람의 관심사가 통계에 남지 않는다.

    tags 테이블은 서버 단위로만 묶여 있어(멤버십과 조인하지 않는다) 지우지 않으면
    나간 사람이 계속 '등록한 멤버'로 잡힌다.
    """
    token_a, token_b, server, _ = await _team(client, register)
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "커피", "tag3": "등산"},
        headers=_headers(token_b),
    )
    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    bob = next(m for m in members if m["display_name"] == "Bob")

    await client.delete(
        f"/servers/{server['id']}/members/{bob['user_id']}", headers=_headers(token_a)
    )

    stats = (
        await client.get(f"/servers/{server['id']}/tags/stats", headers=_headers(token_a))
    ).json()
    assert stats["tagged_members"] == 0
    assert stats["top_tags"] == []


async def test_non_owner_cannot_kick(client: AsyncClient, register):
    token_a, token_b, server, _ = await _team(client, register)
    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    alice = next(m for m in members if m["display_name"] == "Alice")

    # Bob은 방장이 아니라 Alice를 내보낼 수 없다
    resp = await client.delete(
        f"/servers/{server['id']}/members/{alice['user_id']}", headers=_headers(token_b)
    )
    assert resp.status_code == 403


async def test_owner_cannot_kick_self(client: AsyncClient, register):
    token_a, _, server, _ = await _team(client, register)
    members = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    alice = next(m for m in members if m["display_name"] == "Alice")

    resp = await client.delete(
        f"/servers/{server['id']}/members/{alice['user_id']}", headers=_headers(token_a)
    )
    assert resp.status_code == 400
