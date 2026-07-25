"""서버·채널 이름 변경, 삭제, 그리고 멤버 내보내기·나가기.

권한 경계가 핵심이다 — 이름은 멤버면 누구나, 되돌릴 수 없는 것(삭제·내보내기)은
만든 사람만, 자기가 나가는 것은 방장을 뺀 누구나.
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


async def test_owner_deletes_channel_with_its_messages(client: AsyncClient, register):
    """채널을 지우면 그 안의 메시지도 함께 사라진다 (FK ondelete=CASCADE).

    메시지를 미리 남겨두는 이유: 캐스케이드가 빠져 있으면 이 삭제가 FK 위반으로
    터진다. 빈 채널만 지워보는 테스트는 그 실수를 통과시킨다.
    """
    token_a, _, server, first = await _team(client, register)
    extra = (
        await client.post(
            f"/servers/{server['id']}/channels",
            json={"name": "공지"},
            headers=_headers(token_a),
        )
    ).json()
    await client.post(
        f"/channels/{extra['id']}/messages",
        json={"content": "지워질 메시지"},
        headers=_headers(token_a),
    )

    resp = await client.delete(
        f"/servers/{server['id']}/channels/{extra['id']}", headers=_headers(token_a)
    )
    assert resp.status_code == 204, resp.text

    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(token_a))
    ).json()
    assert [c["id"] for c in channels] == [first["id"]]
    # 지워진 채널은 더 이상 열리지 않는다
    assert (
        await client.get(f"/channels/{extra['id']}/messages", headers=_headers(token_a))
    ).status_code == 404


async def test_cannot_delete_last_channel(client: AsyncClient, register):
    """채널이 0개가 되면 그 모임은 들어가도 아무것도 없는 방이 된다."""
    token_a, _, server, channel = await _team(client, register)

    resp = await client.delete(
        f"/servers/{server['id']}/channels/{channel['id']}", headers=_headers(token_a)
    )
    assert resp.status_code == 400

    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(token_a))
    ).json()
    assert len(channels) == 1


async def test_non_owner_cannot_delete_channel(client: AsyncClient, register):
    """이름은 Bob도 바꿀 수 있지만(위 테스트) 삭제는 방장만."""
    token_a, token_b, server, _ = await _team(client, register)
    extra = (
        await client.post(
            f"/servers/{server['id']}/channels",
            json={"name": "공지"},
            headers=_headers(token_a),
        )
    ).json()

    resp = await client.delete(
        f"/servers/{server['id']}/channels/{extra['id']}", headers=_headers(token_b)
    )
    assert resp.status_code == 403
    assert (
        len(
            (
                await client.get(
                    f"/servers/{server['id']}/channels", headers=_headers(token_a)
                )
            ).json()
        )
        == 2
    )


async def test_cannot_delete_channel_of_another_server(client: AsyncClient, register):
    """내가 방장인 서버 id + 남의 채널 id 조합으로 남의 채널을 지울 수 없다."""
    token_a, _, mine, _ = await _team(client, register)
    other_owner = await register(client, "c@test.com", "pass1234", "Carol")
    theirs = (
        await client.post("/servers", json={"name": "남의 방"}, headers=_headers(other_owner))
    ).json()
    their_channel = (
        await client.get(f"/servers/{theirs['id']}/channels", headers=_headers(other_owner))
    ).json()[0]

    resp = await client.delete(
        f"/servers/{mine['id']}/channels/{their_channel['id']}", headers=_headers(token_a)
    )
    assert resp.status_code == 404
    assert (
        await client.get(f"/servers/{theirs['id']}/channels", headers=_headers(other_owner))
    ).json()


async def test_owner_deletes_server(client: AsyncClient, register):
    """모임을 지우면 참여했던 사람 목록에서도 사라지고 채널도 함께 사라진다."""
    token_a, token_b, server, channel = await _team(client, register)
    await client.post(
        f"/channels/{channel['id']}/messages",
        json={"content": "안녕"},
        headers=_headers(token_b),
    )

    resp = await client.delete(f"/servers/{server['id']}", headers=_headers(token_a))
    assert resp.status_code == 204, resp.text

    assert (await client.get("/servers", headers=_headers(token_a))).json() == []
    # 참여만 했던 Bob의 목록에서도 없어져야 한다 (멤버십까지 캐스케이드)
    assert (await client.get("/servers", headers=_headers(token_b))).json() == []
    assert (
        await client.get(f"/channels/{channel['id']}/messages", headers=_headers(token_a))
    ).status_code == 404


async def test_non_owner_cannot_delete_server(client: AsyncClient, register):
    token_a, token_b, server, _ = await _team(client, register)

    resp = await client.delete(f"/servers/{server['id']}", headers=_headers(token_b))
    assert resp.status_code == 403
    assert (await client.get("/servers", headers=_headers(token_a))).json()


async def test_member_leaves_server(client: AsyncClient, register):
    """방장이 내보내 주기를 기다리지 않고 스스로 나갈 수 있다."""
    token_a, token_b, server, _ = await _team(client, register)

    resp = await client.delete(
        f"/servers/{server['id']}/members/me", headers=_headers(token_b)
    )
    assert resp.status_code == 204, resp.text

    remaining = (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_a))
    ).json()
    assert [m["display_name"] for m in remaining] == ["Alice"]
    # 나간 사람에게는 이 모임이 더 이상 보이지 않는다
    assert (await client.get("/servers", headers=_headers(token_b))).json() == []
    assert (
        await client.get(f"/servers/{server['id']}/members", headers=_headers(token_b))
    ).status_code == 403


async def test_left_member_tags_are_removed(client: AsyncClient, register):
    """나가기도 내보내기와 같은 범위를 정리한다 (_remove_membership 공용)."""
    token_a, token_b, server, _ = await _team(client, register)
    await client.put(
        f"/servers/{server['id']}/tags",
        json={"tag1": "축구", "tag2": "커피", "tag3": "등산"},
        headers=_headers(token_b),
    )

    await client.delete(f"/servers/{server['id']}/members/me", headers=_headers(token_b))

    stats = (
        await client.get(f"/servers/{server['id']}/tags/stats", headers=_headers(token_a))
    ).json()
    assert stats["tagged_members"] == 0


async def test_owner_cannot_leave_server(client: AsyncClient, register):
    """방장이 나가면 아무도 정리할 수 없는 모임이 남는다 — 삭제로 안내한다."""
    token_a, _, server, _ = await _team(client, register)

    resp = await client.delete(
        f"/servers/{server['id']}/members/me", headers=_headers(token_a)
    )
    assert resp.status_code == 400
    assert (await client.get("/servers", headers=_headers(token_a))).json()


async def test_non_member_cannot_leave(client: AsyncClient, register):
    """참여한 적 없는 모임에서 나갈 것은 없다 (경로가 'me'라 조용히 204가 되기 쉽다)."""
    _, _, server, _ = await _team(client, register)
    outsider = await register(client, "c@test.com", "pass1234", "Carol")

    resp = await client.delete(
        f"/servers/{server['id']}/members/me", headers=_headers(outsider)
    )
    assert resp.status_code == 404
