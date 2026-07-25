"""방장(판을 연 사람)의 게임 강제 종료.

계약 두 가지: 판을 연 사람만 끝낼 수 있고, 끝내면 판이 "종료"가 아니라
아예 "없음"으로 돌아간다 — 그래야 다음 사람이 곧바로 새 판을 열 수 있다.
"""

import pytest
from httpx import AsyncClient


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _channel(client: AsyncClient, register):
    """Alice가 만든 서버에 Bob이 참여한 채널 하나를 만든다."""
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
    return token_a, token_b, channels[0]["id"]


# 참가만으로 판이 열리는 게임들 — 방장 판정 규칙이 모두 같다는 것까지 확인한다.
JOINABLE = ["bingo", "wordchain", "omok", "tictactoe", "chosung"]


@pytest.mark.parametrize("kind", JOINABLE)
async def test_first_joiner_becomes_host(client: AsyncClient, register, kind: str):
    token_a, token_b, channel_id = await _channel(client, register)
    me = (await client.get("/users/me", headers=_headers(token_a))).json()

    state = (
        await client.post(f"/channels/{channel_id}/{kind}/join", headers=_headers(token_a))
    ).json()
    assert state["host_user_id"] == me["id"]

    # 뒤이어 들어온 사람이 방장을 빼앗지 않는다
    state = (
        await client.post(f"/channels/{channel_id}/{kind}/join", headers=_headers(token_b))
    ).json()
    assert state["host_user_id"] == me["id"]


@pytest.mark.parametrize("kind", JOINABLE)
async def test_host_end_removes_game(client: AsyncClient, register, kind: str):
    token_a, token_b, channel_id = await _channel(client, register)
    await client.post(f"/channels/{channel_id}/{kind}/join", headers=_headers(token_a))
    await client.post(f"/channels/{channel_id}/{kind}/join", headers=_headers(token_b))

    resp = await client.post(
        f"/channels/{channel_id}/games/{kind}/end", headers=_headers(token_a)
    )
    assert resp.status_code == 200

    # "종료"가 아니라 "없음"으로 돌아가야 다음 사람이 새 판을 열 수 있다
    status = (
        await client.get(f"/channels/{channel_id}/games/status", headers=_headers(token_a))
    ).json()
    assert status[kind] == "none"
    assert (
        await client.get(f"/channels/{channel_id}/{kind}", headers=_headers(token_a))
    ).status_code == 404


@pytest.mark.parametrize("kind", JOINABLE)
async def test_non_host_cannot_end(client: AsyncClient, register, kind: str):
    token_a, token_b, channel_id = await _channel(client, register)
    await client.post(f"/channels/{channel_id}/{kind}/join", headers=_headers(token_a))
    await client.post(f"/channels/{channel_id}/{kind}/join", headers=_headers(token_b))

    resp = await client.post(
        f"/channels/{channel_id}/games/{kind}/end", headers=_headers(token_b)
    )
    assert resp.status_code == 403
    # 남의 판은 그대로 살아 있어야 한다
    status = (
        await client.get(f"/channels/{channel_id}/games/status", headers=_headers(token_b))
    ).json()
    assert status[kind] != "none"


async def test_balance_host_can_end(client: AsyncClient, register):
    """밸런스게임은 참가가 아니라 '게시'로 열린다 — 게시한 사람이 방장."""
    token_a, token_b, channel_id = await _channel(client, register)
    await client.post(
        f"/channels/{channel_id}/balance/start",
        json={"option_a": "산", "option_b": "바다"},
        headers=_headers(token_a),
    )

    assert (
        await client.post(
            f"/channels/{channel_id}/games/balance/end", headers=_headers(token_b)
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/channels/{channel_id}/games/balance/end", headers=_headers(token_a)
        )
    ).status_code == 200
    status = (
        await client.get(f"/channels/{channel_id}/games/status", headers=_headers(token_a))
    ).json()
    assert status["balance"] == "none"


async def test_end_without_game_404(client: AsyncClient, register):
    token_a, _, channel_id = await _channel(client, register)
    resp = await client.post(
        f"/channels/{channel_id}/games/bingo/end", headers=_headers(token_a)
    )
    assert resp.status_code == 404


async def test_unknown_kind_404(client: AsyncClient, register):
    token_a, _, channel_id = await _channel(client, register)
    resp = await client.post(
        f"/channels/{channel_id}/games/mahjong/end", headers=_headers(token_a)
    )
    assert resp.status_code == 404


async def test_non_member_cannot_end(client: AsyncClient, register):
    token_a, _, channel_id = await _channel(client, register)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(token_a))
    outsider = await register(client, "c@test.com", "pass1234", "Carol")

    resp = await client.post(
        f"/channels/{channel_id}/games/bingo/end", headers=_headers(outsider)
    )
    assert resp.status_code == 403


async def test_new_round_hands_host_to_whoever_reopens(client: AsyncClient, register):
    """라운드마다 참가자가 비워지는 게임(빙고)에서는 다시 연 사람이 새 방장이 된다."""
    token_a, token_b, channel_id = await _channel(client, register)
    bob = (await client.get("/users/me", headers=_headers(token_b))).json()

    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(token_a))
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(token_b))
    await client.post(f"/channels/{channel_id}/bingo/start", headers=_headers(token_a))

    # 판을 끝난 상태로 만든 뒤(승부까지 갈 필요 없이 방장이 접었다가 다시 여는 흐름),
    # 다음 판을 Bob이 열면 방장은 Bob이다.
    await client.post(f"/channels/{channel_id}/games/bingo/end", headers=_headers(token_a))
    state = (
        await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(token_b))
    ).json()
    assert state["host_user_id"] == bob["id"]
