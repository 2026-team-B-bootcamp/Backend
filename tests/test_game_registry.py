"""채널당 게임 1종 제한을 폐기한 뒤의 정책 검증:
서로 다른 게임 종류가 한 채널에서 동시에 열리고(공존), games/status로 각 상태가 집계된다.
"""

from httpx import AsyncClient

from app.services.bingo.store import BingoGameStore, get_bingo_store
from app.services.wordchain.store import WordChainStore, get_wordchain_store


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _setup_channel(client: AsyncClient, register):
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


async def test_different_games_coexist_in_channel(client: AsyncClient, register):
    from app.main import app

    app.dependency_overrides[get_bingo_store] = lambda: BingoGameStore()
    app.dependency_overrides[get_wordchain_store] = lambda: WordChainStore()

    token_a, token_b, channel_id = await _setup_channel(client, register)

    # 빙고를 연 상태에서 끝말잇기도 열 수 있어야 한다(예전엔 409로 막혔음).
    joined_bingo = await client.post(
        f"/channels/{channel_id}/bingo/join", headers=_headers(token_a)
    )
    assert joined_bingo.status_code == 200

    joined_wc = await client.post(
        f"/channels/{channel_id}/wordchain/join", headers=_headers(token_b)
    )
    assert joined_wc.status_code == 200

    app.dependency_overrides.pop(get_bingo_store, None)
    app.dependency_overrides.pop(get_wordchain_store, None)


async def test_games_status_aggregates(client: AsyncClient, register):
    from app.main import app

    app.dependency_overrides[get_bingo_store] = lambda: BingoGameStore()

    token_a, token_b, channel_id = await _setup_channel(client, register)

    # 아무 게임도 없을 때는 전부 none
    empty = await client.get(f"/channels/{channel_id}/games/status", headers=_headers(token_a))
    assert empty.status_code == 200
    assert empty.json()["bingo"] == "none"

    # 한 명 참여하면 대기, 2명 모여 시작하면 진행중으로 잡힌다
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(token_a))
    waiting = (
        await client.get(f"/channels/{channel_id}/games/status", headers=_headers(token_a))
    ).json()
    assert waiting["bingo"] == "waiting"

    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(token_b))
    await client.post(f"/channels/{channel_id}/bingo/start", headers=_headers(token_a))
    playing = (
        await client.get(f"/channels/{channel_id}/games/status", headers=_headers(token_a))
    ).json()
    assert playing["bingo"] == "playing"

    app.dependency_overrides.pop(get_bingo_store, None)
