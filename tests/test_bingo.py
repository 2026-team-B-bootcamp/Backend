import pytest
from httpx import AsyncClient

from app.services.bingo import store as store_module
from app.services.bingo.store import BingoGameStore, get_bingo_store


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fresh_store():
    from app.main import app

    store = BingoGameStore()
    app.dependency_overrides[get_bingo_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_bingo_store, None)


async def _setup_channel(client: AsyncClient, register, members: int):
    """서버를 만들고 members 명을 참여시킨 뒤 (tokens, 기본 채널 id)를 돌려준다."""
    tokens = []
    token_a = await register(client, "a@test.com", "pass1234", "Alice")
    tokens.append(token_a)
    server = (
        await client.post("/servers", json={"name": "Team"}, headers=_headers(token_a))
    ).json()
    names = ["Bob", "Carol", "Dave", "Erin"]
    for i in range(1, members):
        token = await register(
            client, f"{names[i - 1].lower()}@test.com", "pass1234", names[i - 1]
        )
        await client.post(
            "/servers/join",
            json={"invite_code": server["invite_code"]},
            headers=_headers(token),
        )
        tokens.append(token)
    channels = (
        await client.get(f"/servers/{server['id']}/channels", headers=_headers(token_a))
    ).json()
    return tokens, channels[0]["id"]


async def test_multiple_members_join_same_game(client: AsyncClient, register, fresh_store):
    tokens, channel_id = await _setup_channel(client, register, 3)

    states = []
    for token in tokens:
        resp = await client.post(
            f"/channels/{channel_id}/bingo/join", headers=_headers(token)
        )
        assert resp.status_code == 200, resp.text
        states.append(resp.json())

    # Each player gets their own full board.
    for state in states:
        assert sorted(state["my_board"]) == list(range(1, 26))
    # After all three joined, the game lists three players, round 1.
    final = states[-1]
    assert final["round"] == 1
    assert len(final["players"]) == 3


async def test_non_joined_click_409(client: AsyncClient, register, fresh_store):
    tokens, channel_id = await _setup_channel(client, register, 2)
    # Only Alice joins.
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))

    resp = await client.post(
        f"/channels/{channel_id}/bingo/click",
        json={"number": 1},
        headers=_headers(tokens[1]),
    )
    assert resp.status_code == 409


async def test_get_never_leaks_opponent_board(client: AsyncClient, register, fresh_store):
    tokens, channel_id = await _setup_channel(client, register, 2)
    a_state = (
        await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))
    ).json()
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[1]))

    body = (
        await client.get(f"/channels/{channel_id}/bingo", headers=_headers(tokens[0]))
    ).json()
    assert body["my_board"] == a_state["my_board"]
    for player in body["players"]:
        assert set(player.keys()) == {"user_id", "display_name", "completed_lines"}
        assert "board" not in player
    assert "board" not in body


async def test_get_my_board_null_when_not_joined(client: AsyncClient, register, fresh_store):
    tokens, channel_id = await _setup_channel(client, register, 2)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))

    body = (
        await client.get(f"/channels/{channel_id}/bingo", headers=_headers(tokens[1]))
    ).json()
    assert body["my_board"] is None


async def test_winner_on_three_lines(client: AsyncClient, register, fresh_store, monkeypatch):
    # Deterministic ordered board so calling 1..15 completes rows 0,1,2 (3 lines).
    monkeypatch.setattr(store_module, "generate_board", lambda: list(range(1, 26)))
    tokens, channel_id = await _setup_channel(client, register, 2)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[1]))
    await client.post(f"/channels/{channel_id}/bingo/start", headers=_headers(tokens[0]))

    last = None
    for number in range(1, 16):
        last = (
            await client.post(
                f"/channels/{channel_id}/bingo/click",
                json={"number": number},
                headers=_headers(tokens[0]),
            )
        ).json()
    assert last["winner_user_id"] is not None
    winner = next(p for p in last["players"] if p["user_id"] == last["winner_user_id"])
    assert winner["completed_lines"] >= 3


async def test_start_requires_two_players(client: AsyncClient, register, fresh_store):
    tokens, channel_id = await _setup_channel(client, register, 2)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))
    # 혼자서는 시작할 수 없다
    solo = await client.post(f"/channels/{channel_id}/bingo/start", headers=_headers(tokens[0]))
    assert solo.status_code == 409
    # 대기 중에는 클릭도 막힌다
    click = await client.post(
        f"/channels/{channel_id}/bingo/click", json={"number": 1}, headers=_headers(tokens[0])
    )
    assert click.status_code == 409
    # 2명이 되면 시작 가능 → 진행중
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[1]))
    started = await client.post(
        f"/channels/{channel_id}/bingo/start", headers=_headers(tokens[0])
    )
    assert started.status_code == 200
    assert started.json()["status"] == "playing"


async def test_join_while_playing_blocked_spectate(client: AsyncClient, register, fresh_store):
    tokens, channel_id = await _setup_channel(client, register, 3)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[1]))
    await client.post(f"/channels/{channel_id}/bingo/start", headers=_headers(tokens[0]))

    # 진행 중이면 세 번째 사람은 참가 불가(관전만)
    late = await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[2]))
    assert late.status_code == 409
    # 관전자는 조회로 상태를 볼 수 있고 my_board는 없다
    spectate = (
        await client.get(f"/channels/{channel_id}/bingo", headers=_headers(tokens[2]))
    ).json()
    assert spectate["status"] == "playing"
    assert spectate["my_board"] is None
    assert len(spectate["players"]) == 2


async def test_rejoin_after_win_resets_round(
    client: AsyncClient, register, fresh_store, monkeypatch
):
    monkeypatch.setattr(store_module, "generate_board", lambda: list(range(1, 26)))
    tokens, channel_id = await _setup_channel(client, register, 2)
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))
    await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[1]))
    await client.post(f"/channels/{channel_id}/bingo/start", headers=_headers(tokens[0]))
    for number in range(1, 16):
        await client.post(
            f"/channels/{channel_id}/bingo/click",
            json={"number": number},
            headers=_headers(tokens[0]),
        )

    reset = (
        await client.post(f"/channels/{channel_id}/bingo/join", headers=_headers(tokens[0]))
    ).json()
    assert reset["round"] == 2
    assert reset["called_numbers"] == []
    assert reset["winner_user_id"] is None
    # Only the rejoining player is present in the fresh round.
    assert [p["user_id"] for p in reset["players"]] == [reset["players"][0]["user_id"]]
    assert sorted(reset["my_board"]) == list(range(1, 26))
