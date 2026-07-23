"""대기·종료 상태로 방치된 게임이 30초 뒤 자동으로 사라지는지 검증한다.

실제로 30초를 기다리지 않고, Redis에 걸린 TTL 값을 직접 확인한다 —
"몇 초 뒤에 사라지도록 예약됐는가"가 곧 이 기능의 계약이다.
"""

from httpx import AsyncClient

from app.services.game_ttl import IDLE_TTL_SECONDS, ttl_for


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_ttl_for_picks_short_ttl_when_idle():
    assert ttl_for("waiting", 3600) == IDLE_TTL_SECONDS
    assert ttl_for("finished", 3600) == IDLE_TTL_SECONDS
    # 진행 중인 게임은 원래의 긴 TTL을 유지한다
    assert ttl_for("playing", 3600) == 3600


async def _setup(client: AsyncClient, register, members: int = 2):
    tokens = [await register(client, "a@test.com", "pass1234", "Alice")]
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(tokens[0]))
    ).json()
    names = ["Bob", "Carol"]
    for i in range(members - 1):
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
    return tokens, channels[0]["id"]


async def test_waiting_game_expires_in_30s(client: AsyncClient, register, fake_redis):
    """게임을 열어놓고 아무도 안 들어오면 30초 뒤 사라지도록 예약된다."""
    tokens, channel_id = await _setup(client, register)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))

    ttl = await fake_redis.ttl(f"game:bingo:{channel_id}")
    assert 0 < ttl <= IDLE_TTL_SECONDS


async def test_playing_game_keeps_long_ttl(client: AsyncClient, register, fake_redis):
    """진행 중인 게임은 30초 만에 사라지면 안 된다."""
    tokens, channel_id = await _setup(client, register)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[1]))
    await client.post(f"/channels/{channel_id}/bingo/start", headers=_headers(tokens[0]))

    ttl = await fake_redis.ttl(f"game:bingo:{channel_id}")
    assert ttl > IDLE_TTL_SECONDS


async def test_finished_game_expires_in_30s(
    client: AsyncClient, register, fake_redis, monkeypatch
):
    """승부가 난 뒤 아무도 새 판을 안 열면 30초 뒤 사라지도록 예약된다."""
    from app.services.bingo import store as store_module

    monkeypatch.setattr(store_module, "generate_board", lambda: list(range(1, 26)))
    tokens, channel_id = await _setup(client, register)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[1]))
    await client.post(f"/channels/{channel_id}/bingo/start", headers=_headers(tokens[0]))
    for i, number in enumerate(range(1, 16)):
        await client.post(
            f"/channels/{channel_id}/bingo/click",
            json={"number": number},
            headers=_headers(tokens[i % 2]),
        )

    state = (
        await client.get(f"/channels/{channel_id}/bingo", headers=_headers(tokens[0]))
    ).json()
    assert state["status"] == "finished"
    ttl = await fake_redis.ttl(f"game:bingo:{channel_id}")
    assert 0 < ttl <= IDLE_TTL_SECONDS
