"""메시지 소프트 삭제와 첫 입장 환영 카드 테스트."""

from httpx import AsyncClient


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _setup(client: AsyncClient, register, extra_members: int = 0):
    """서버 + 기본 채널을 만들고 (토큰 목록, 채널 id)를 돌려준다."""
    tokens = [await register(client, "a@test.com", "pass1234", "Alice")]
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(tokens[0]))
    ).json()
    names = ["Bob", "Carol"]
    for i in range(extra_members):
        token = await register(client, f"{names[i].lower()}@test.com", "pass1234", names[i])
        await client.post(
            "/servers/join",
            json={"invite_code": server["invite_code"]},
            headers=_headers(token),
        )
        tokens.append(token)
    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(tokens[0]))
    ).json()
    return tokens, channels[0]["id"], server["id"]


async def test_delete_own_message_hides_it(client: AsyncClient, register):
    tokens, channel_id, _ = await _setup(client, register)
    sent = (
        await client.post(
            f"/channels/{channel_id}/messages",
            json={"content": "지울 메시지"},
            headers=_headers(tokens[0]),
        )
    ).json()

    resp = await client.delete(
        f"/channels/{channel_id}/messages/{sent['id']}", headers=_headers(tokens[0])
    )
    assert resp.status_code == 204

    listed = (
        await client.get(f"/channels/{channel_id}/messages", headers=_headers(tokens[0]))
    ).json()
    assert all(m["id"] != sent["id"] for m in listed)


async def test_cannot_delete_someone_elses_message(client: AsyncClient, register):
    tokens, channel_id, _ = await _setup(client, register, extra_members=1)
    sent = (
        await client.post(
            f"/channels/{channel_id}/messages",
            json={"content": "Alice의 메시지"},
            headers=_headers(tokens[0]),
        )
    ).json()

    # Bob이 Alice 메시지를 지우려 하면 404 (존재 여부를 흘리지 않기 위해 403이 아니다)
    resp = await client.delete(
        f"/channels/{channel_id}/messages/{sent['id']}", headers=_headers(tokens[1])
    )
    assert resp.status_code == 404

    listed = (
        await client.get(f"/channels/{channel_id}/messages", headers=_headers(tokens[0]))
    ).json()
    assert any(m["id"] == sent["id"] for m in listed)


async def test_delete_is_idempotent(client: AsyncClient, register):
    tokens, channel_id, _ = await _setup(client, register)
    sent = (
        await client.post(
            f"/channels/{channel_id}/messages",
            json={"content": "두 번 지운다"},
            headers=_headers(tokens[0]),
        )
    ).json()
    first = await client.delete(
        f"/channels/{channel_id}/messages/{sent['id']}", headers=_headers(tokens[0])
    )
    second = await client.delete(
        f"/channels/{channel_id}/messages/{sent['id']}", headers=_headers(tokens[0])
    )
    assert first.status_code == 204
    assert second.status_code == 204


async def test_deleted_message_excluded_from_pagination(client: AsyncClient, register):
    """삭제된 메시지는 이전 메시지 조회(before_id)에서도 빠진다."""
    tokens, channel_id, _ = await _setup(client, register)
    ids = []
    for i in range(3):
        sent = (
            await client.post(
                f"/channels/{channel_id}/messages",
                json={"content": f"메시지 {i}"},
                headers=_headers(tokens[0]),
            )
        ).json()
        ids.append(sent["id"])

    await client.delete(
        f"/channels/{channel_id}/messages/{ids[0]}", headers=_headers(tokens[0])
    )
    older = (
        await client.get(
            f"/channels/{channel_id}/messages?before_id={ids[2]}", headers=_headers(tokens[0])
        )
    ).json()
    assert [m["id"] for m in older] == [ids[1]]


async def test_welcome_card_created_once(client: AsyncClient, register):
    tokens, channel_id, server_id = await _setup(client, register)
    await client.put(
        f"/servers/{server_id}/tags",
        json={"tag1": "캠핑", "tag2": "재즈", "tag3": "커피"},
        headers=_headers(tokens[0]),
    )

    first = await client.post(
        f"/channels/{channel_id}/messages/welcome", headers=_headers(tokens[0])
    )
    assert first.status_code == 200
    card = first.json()
    assert card is not None
    assert card["kind"] == "welcome"
    # 관심사가 문구에 반영되고, 플레이스홀더가 실제 이름으로 치환돼 있어야 한다
    assert "Alice" in card["content"]
    assert "{이름}" not in card["content"]

    # 두 번째 호출은 아무것도 만들지 않는다 (이미 이 채널에 메시지가 있으므로)
    second = await client.post(
        f"/channels/{channel_id}/messages/welcome", headers=_headers(tokens[0])
    )
    assert second.status_code == 200
    assert second.json() is None

    listed = (
        await client.get(f"/channels/{channel_id}/messages", headers=_headers(tokens[0]))
    ).json()
    assert len([m for m in listed if m["kind"] == "welcome"]) == 1


async def test_welcome_skipped_when_already_spoke(client: AsyncClient, register):
    """이미 대화한 적 있는 사람에겐 환영 카드를 만들지 않는다."""
    tokens, channel_id, _ = await _setup(client, register)
    await client.post(
        f"/channels/{channel_id}/messages",
        json={"content": "안녕하세요"},
        headers=_headers(tokens[0]),
    )

    resp = await client.post(
        f"/channels/{channel_id}/messages/welcome", headers=_headers(tokens[0])
    )
    assert resp.status_code == 200
    assert resp.json() is None


async def test_message_carries_avatar_url(client: AsyncClient, register):
    """메시지 응답에 작성자 아바타 URL이 실린다 (없으면 null)."""
    tokens, channel_id, _ = await _setup(client, register)
    sent = (
        await client.post(
            f"/channels/{channel_id}/messages",
            json={"content": "안녕"},
            headers=_headers(tokens[0]),
        )
    ).json()
    assert "avatar_url" in sent
    assert sent["avatar_url"] is None
    assert sent["kind"] == "user"


async def test_welcome_skipped_without_tags(client: AsyncClient, register):
    """태그 등록 전에는 카드를 만들지 않는다.

    자기소개의 알맹이가 태그다. 태그 없이 먼저 띄우면 맹탕 카드가 "채널당 1회"를
    소진해버려, 정작 태그를 등록한 뒤에는 카드가 안 나온다.
    """
    tokens, channel_id, _ = await _setup(client, register)

    before = await client.post(
        f"/channels/{channel_id}/messages/welcome", headers=_headers(tokens[0])
    )
    assert before.status_code == 200
    assert before.json() is None
    # 카드를 안 만들었으니 채널은 여전히 비어 있어야 한다
    listed = (
        await client.get(f"/channels/{channel_id}/messages", headers=_headers(tokens[0]))
    ).json()
    assert listed == []


async def test_welcome_created_after_tags_registered(client: AsyncClient, register):
    """태그를 등록한 뒤 다시 부르면 그 태그를 반영한 카드가 만들어진다."""
    tokens, channel_id, server_id = await _setup(client, register)

    # 태그 없이 한 번 호출 — 아무 일도 일어나지 않는다
    await client.post(f"/channels/{channel_id}/messages/welcome", headers=_headers(tokens[0]))

    await client.put(
        f"/servers/{server_id}/tags",
        json={"tag1": "캠핑", "tag2": "재즈", "tag3": "커피"},
        headers=_headers(tokens[0]),
    )
    after = await client.post(
        f"/channels/{channel_id}/messages/welcome", headers=_headers(tokens[0])
    )
    card = after.json()
    assert card is not None
    assert card["kind"] == "welcome"
    # 등록한 태그가 문구에 반영되고, 카드에도 태그가 실려 온다
    assert any(tag in card["content"] for tag in ("캠핑", "재즈", "커피"))
    assert card["tags"] == ["캠핑", "재즈", "커피"]
