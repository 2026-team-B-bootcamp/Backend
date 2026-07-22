from httpx import AsyncClient


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _setup_server_with_two_members(client: AsyncClient, register):
    """서버 + 기본 채널을 만들고 두 명을 참여시킨다. (token_a, token_b, server_id, channel_id)"""
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
    return token_a, token_b, server["id"], channels[0]["id"]


async def test_send_then_list(client: AsyncClient, register):
    token_a, token_b, _server_id, channel_id = await _setup_server_with_two_members(
        client, register
    )

    sent = await client.post(
        f"/channels/{channel_id}/messages",
        json={"content": "hello team"},
        headers=_headers(token_a),
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["content"] == "hello team"
    assert body["display_name"] == "Alice"

    listed = (
        await client.get(f"/channels/{channel_id}/messages", headers=_headers(token_b))
    ).json()
    assert len(listed) == 1
    assert listed[0]["content"] == "hello team"
    assert listed[0]["display_name"] == "Alice"


async def test_message_carries_sender_tags(client: AsyncClient, register):
    token_a, _token_b, server_id, channel_id = await _setup_server_with_two_members(
        client, register
    )

    await client.put(
        f"/servers/{server_id}/tags",
        json={"tag1": "축구", "tag2": "영화", "tag3": "커피"},
        headers=_headers(token_a),
    )
    sent = await client.post(
        f"/channels/{channel_id}/messages",
        json={"content": "hi"},
        headers=_headers(token_a),
    )
    assert sent.json()["tags"] == ["축구", "영화", "커피"]

    listed = (
        await client.get(f"/channels/{channel_id}/messages", headers=_headers(token_a))
    ).json()
    assert listed[0]["tags"] == ["축구", "영화", "커피"]


async def test_after_id_cursor_returns_only_new(client: AsyncClient, register):
    token_a, _token_b, _server_id, channel_id = await _setup_server_with_two_members(
        client, register
    )

    ids = []
    for text in ("m1", "m2", "m3"):
        resp = await client.post(
            f"/channels/{channel_id}/messages",
            json={"content": text},
            headers=_headers(token_a),
        )
        ids.append(resp.json()["id"])

    new = (
        await client.get(
            f"/channels/{channel_id}/messages",
            params={"after_id": ids[0]},
            headers=_headers(token_a),
        )
    ).json()
    assert [m["id"] for m in new] == ids[1:]

    empty = (
        await client.get(
            f"/channels/{channel_id}/messages",
            params={"after_id": ids[-1]},
            headers=_headers(token_a),
        )
    ).json()
    assert empty == []


async def test_before_id_cursor_pages_history(client: AsyncClient, register):
    """무한 스크롤용 before_id 커서 — 커서 이전 메시지를 오름차순으로 돌려준다."""
    token_a, _token_b, _server_id, channel_id = await _setup_server_with_two_members(
        client, register
    )

    ids = []
    for text in ("m1", "m2", "m3", "m4", "m5"):
        resp = await client.post(
            f"/channels/{channel_id}/messages",
            json={"content": text},
            headers=_headers(token_a),
        )
        ids.append(resp.json()["id"])

    # 중간 커서: 그보다 오래된 메시지만, 오래된 순으로
    older = (
        await client.get(
            f"/channels/{channel_id}/messages",
            params={"before_id": ids[2]},
            headers=_headers(token_a),
        )
    ).json()
    assert [m["id"] for m in older] == ids[:2]

    # 첫 메시지 커서: 더 오래된 게 없으니 빈 목록 (클라이언트는 hasMore=false 처리)
    empty = (
        await client.get(
            f"/channels/{channel_id}/messages",
            params={"before_id": ids[0]},
            headers=_headers(token_a),
        )
    ).json()
    assert empty == []


async def test_non_member_forbidden(client: AsyncClient, register):
    token_a, _token_b, _server_id, channel_id = await _setup_server_with_two_members(
        client, register
    )
    token_c = await register(client, "c@test.com", "pass1234", "Carol")

    posted = await client.post(
        f"/channels/{channel_id}/messages",
        json={"content": "intruder"},
        headers=_headers(token_c),
    )
    assert posted.status_code == 403

    listed = await client.get(
        f"/channels/{channel_id}/messages", headers=_headers(token_c)
    )
    assert listed.status_code == 403
