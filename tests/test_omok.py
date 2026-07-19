import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.services.omok.store import (
    BLACK,
    BOARD_SIZE,
    EMPTY,
    WHITE,
    OmokStore,
    find_winning_line,
    get_omok_store,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _blank() -> list[list[int]]:
    return [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]


# ---------- 승리 판정 (네 방향) ----------


def test_win_horizontal():
    board = _blank()
    for c in range(3, 8):
        board[7][c] = BLACK
    line = find_winning_line(board, 7, 7, BLACK)
    assert line is not None
    assert len(line) == 5
    assert [7, 3] in line and [7, 7] in line


def test_win_vertical():
    board = _blank()
    for r in range(2, 7):
        board[r][4] = WHITE
    line = find_winning_line(board, 4, 4, WHITE)
    assert line is not None
    assert len(line) == 5


def test_win_diagonal_down_right():
    board = _blank()
    for i in range(5):
        board[i][i] = BLACK
    line = find_winning_line(board, 4, 4, BLACK)
    assert line is not None
    assert len(line) == 5


def test_win_diagonal_down_left():
    board = _blank()
    for i in range(5):
        board[i][10 - i] = WHITE
    line = find_winning_line(board, 4, 6, WHITE)
    assert line is not None
    assert len(line) == 5


def test_four_in_a_row_is_not_a_win():
    board = _blank()
    for c in range(0, 4):
        board[0][c] = BLACK
    assert find_winning_line(board, 0, 3, BLACK) is None


# ---------- 스토어 로직 ----------


async def test_two_players_get_black_and_white():
    store = OmokStore(clock=FakeClock())
    game = await store.join(1, 1, "Alice")
    assert game.status == "waiting"
    assert game.players[0].color == BLACK
    game = await store.join(1, 2, "Bob")
    assert game.status == "playing"
    assert game.player_by_color(WHITE).user_id == 2
    assert game.turn == BLACK


async def test_third_joiner_rejected():
    store = OmokStore(clock=FakeClock())
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    with pytest.raises(HTTPException) as exc:
        await store.join(1, 3, "Carol")
    assert exc.value.status_code == 409


async def test_join_is_idempotent_for_seated_player():
    store = OmokStore(clock=FakeClock())
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.join(1, 1, "Alice")
    assert len(game.players) == 2


async def test_out_of_turn_rejected():
    store = OmokStore(clock=FakeClock())
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    # 흑(1)이 먼저인데 백(2)이 두려고 하면 거절
    with pytest.raises(HTTPException) as exc:
        await store.place(1, 2, 0, 0)
    assert exc.value.status_code == 409


async def test_occupied_cell_rejected():
    store = OmokStore(clock=FakeClock())
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.place(1, 1, 7, 7)  # 흑, 이제 백 차례
    with pytest.raises(HTTPException) as exc:
        await store.place(1, 2, 7, 7)  # 백이 같은 자리에 두려고
    assert exc.value.status_code == 409


async def test_full_game_black_wins_horizontally():
    store = OmokStore(clock=FakeClock())
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    for i in range(4):
        await store.place(1, 1, 0, i)  # 흑
        await store.place(1, 2, 5, i)  # 백
    game = await store.place(1, 1, 0, 4)  # 흑 5목 완성
    assert game.status == "finished"
    assert game.winner_user_id == 1
    assert game.winning_line is not None and len(game.winning_line) == 5


async def test_turn_alternates():
    store = OmokStore(clock=FakeClock())
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.place(1, 1, 0, 0)
    assert game.turn == WHITE
    game = await store.place(1, 2, 1, 1)
    assert game.turn == BLACK


async def test_draw_when_board_fills_without_winner():
    store = OmokStore(clock=FakeClock())
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.get(1)
    # 어느 방향으로도 최대 연속 2칸뿐인 패턴으로 판을 가득 채운다 (승자 없음).
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            game.board[r][c] = BLACK if ((r + 2 * c) % 4 < 2) else WHITE
    game.board[0][0] = EMPTY  # (0,0)은 패턴상 흑 자리
    game.move_count = BOARD_SIZE * BOARD_SIZE - 1
    game.turn = BLACK
    result = await store.place(1, 1, 0, 0)
    assert result.status == "finished"
    assert result.winner_user_id is None
    assert result.winning_line is None


async def test_join_after_finish_opens_new_round():
    store = OmokStore(clock=FakeClock())
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    for i in range(4):
        await store.place(1, 1, 0, i)
        await store.place(1, 2, 5, i)
    finished = await store.place(1, 1, 0, 4)
    assert finished.status == "finished"

    game = await store.join(1, 1, "Alice")
    assert game.status == "playing"
    assert game.winner_user_id is None
    assert all(cell == EMPTY for row in game.board for cell in row)
    assert game.turn == BLACK


# ---------- API 플로우 ----------


@pytest.fixture
def fresh_omok_store():
    from app.main import app

    store = OmokStore()
    app.dependency_overrides[get_omok_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_omok_store, None)


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


async def test_api_full_flow(client: AsyncClient, register, fresh_omok_store):
    token_a, token_b, channel_id = await _setup_channel(client, register)

    joined = await client.post(f"/channels/{channel_id}/omok/join", headers=_headers(token_a))
    assert joined.status_code == 200
    assert joined.json()["status"] == "waiting"

    started = await client.post(f"/channels/{channel_id}/omok/join", headers=_headers(token_b))
    assert started.status_code == 200
    assert started.json()["status"] == "playing"
    assert started.json()["turn_user_id"] is not None

    placed = await client.post(
        f"/channels/{channel_id}/omok/place",
        json={"row": 7, "col": 7},
        headers=_headers(token_a),
    )
    assert placed.status_code == 200
    assert placed.json()["board"][7][7] == BLACK

    # 백 차례인데 흑이 또 두면 409
    again = await client.post(
        f"/channels/{channel_id}/omok/place",
        json={"row": 8, "col": 8},
        headers=_headers(token_a),
    )
    assert again.status_code == 409

    fetched = await client.get(f"/channels/{channel_id}/omok", headers=_headers(token_b))
    assert fetched.status_code == 200
    assert fetched.json()["board"][7][7] == BLACK


async def test_api_non_member_403(client: AsyncClient, register, fresh_omok_store):
    _token_a, _token_b, channel_id = await _setup_channel(client, register)
    token_c = await register(client, "c@test.com", "pass1234", "Carol")
    resp = await client.post(f"/channels/{channel_id}/omok/join", headers=_headers(token_c))
    assert resp.status_code == 403
