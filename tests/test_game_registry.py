from httpx import AsyncClient

from app.services.bingo.store import BingoGameStore, get_bingo_store
from app.services.wheel.store import WheelStore, get_wheel_store
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


async def test_second_game_kind_blocked_while_first_active(client: AsyncClient, register):
    from app.main import app

    app.dependency_overrides[get_bingo_store] = lambda: BingoGameStore()
    app.dependency_overrides[get_wordchain_store] = lambda: WordChainStore()

    token_a, token_b, channel_id = await _setup_channel(client, register)

    joined = await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(token_a))
    assert joined.status_code == 200

    blocked = await client.post(
        f"/channels/{channel_id}/wordchain/join", headers=_headers(token_b)
    )
    assert blocked.status_code == 409

    # Same kind can keep joining — it's not a "different" game.
    same_kind = await client.post(
        f"/channels/{channel_id}/bingo/join", headers=_headers(token_b)
    )
    assert same_kind.status_code == 200

    app.dependency_overrides.pop(get_bingo_store, None)
    app.dependency_overrides.pop(get_wordchain_store, None)


async def test_lock_releases_after_wheel_reset(client: AsyncClient, register):
    from app.main import app

    app.dependency_overrides[get_wheel_store] = lambda: WheelStore()
    app.dependency_overrides[get_wordchain_store] = lambda: WordChainStore()

    token_a, token_b, channel_id = await _setup_channel(client, register)

    await client.post(f"/channels/{channel_id}/wheel/join", headers=_headers(token_a))
    blocked = await client.post(
        f"/channels/{channel_id}/wordchain/join", headers=_headers(token_b)
    )
    assert blocked.status_code == 409

    await client.post(f"/channels/{channel_id}/wheel/reset", headers=_headers(token_a))

    freed = await client.post(
        f"/channels/{channel_id}/wordchain/join", headers=_headers(token_b)
    )
    assert freed.status_code == 200

    app.dependency_overrides.pop(get_wheel_store, None)
    app.dependency_overrides.pop(get_wordchain_store, None)
