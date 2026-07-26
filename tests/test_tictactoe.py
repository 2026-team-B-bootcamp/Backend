import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.services.tictactoe.store import (
    BOARD_SIZE,
    EMPTY,
    O,
    TicTacToeStore,
    X,
    find_winning_line,
    get_tictactoe_store,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _blank() -> list[list[int]]:
    return [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]


# ---------- 승리 판정 (네 방향, 3x3/3목) ----------


def test_win_horizontal():
    board = _blank()
    for c in range(BOARD_SIZE):
        board[1][c] = X
    line = find_winning_line(board, 1, 1, X)
    assert line is not None
    assert len(line) == 3
    assert [1, 0] in line and [1, 2] in line


def test_win_vertical():
    board = _blank()
    for r in range(BOARD_SIZE):
        board[r][1] = O
    line = find_winning_line(board, 1, 1, O)
    assert line is not None
    assert len(line) == 3


def test_win_diagonal_down_right():
    board = _blank()
    for i in range(BOARD_SIZE):
        board[i][i] = X
    line = find_winning_line(board, 1, 1, X)
    assert line is not None
    assert len(line) == 3


def test_win_diagonal_down_left():
    board = _blank()
    for i in range(BOARD_SIZE):
        board[i][BOARD_SIZE - 1 - i] = O
    line = find_winning_line(board, 1, 1, O)
    assert line is not None
    assert len(line) == 3


def test_two_in_a_row_is_not_a_win():
    board = _blank()
    board[0][0] = X
    board[0][1] = X
    assert find_winning_line(board, 0, 1, X) is None


# ---------- 스토어 로직 ----------


async def test_two_players_get_x_and_o():
    store = TicTacToeStore()
    game = await store.join(1, 1, "Alice")
    assert game.status == "waiting"
    assert game.players[0].mark == X
    game = await store.join(1, 2, "Bob")
    assert game.status == "playing"
    assert game.player_by_mark(O).user_id == 2
    assert game.turn == X


async def test_third_joiner_rejected():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    with pytest.raises(HTTPException) as exc:
        await store.join(1, 3, "Carol")
    assert exc.value.status_code == 409


async def test_join_is_idempotent_for_seated_player():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.join(1, 1, "Alice")
    assert len(game.players) == 2


async def test_out_of_turn_rejected():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    # X(1)가 먼저인데 O(2)가 두려고 하면 거절
    with pytest.raises(HTTPException) as exc:
        await store.place(1, 2, 0, 0)
    assert exc.value.status_code == 409


async def test_occupied_cell_rejected_with_tictactoe_wording():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.place(1, 1, 1, 1)  # X, 이제 O 차례
    with pytest.raises(HTTPException) as exc:
        await store.place(1, 2, 1, 1)  # O가 같은 자리에 두려고
    assert exc.value.status_code == 409
    # 오목 store는 "이미 돌이 놓인 자리예요"를 쓴다 — 문구가 다르다. 통합 리팩토링 시
    # 이 문구가 오목 것으로 뒤바뀌는 회귀를 잡는 유일한 방어선이므로 그대로 단언한다.
    assert exc.value.detail == "이미 표시된 칸이에요"


async def test_place_out_of_bounds_422():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    with pytest.raises(HTTPException) as exc:
        await store.place(1, 1, BOARD_SIZE, 0)
    assert exc.value.status_code == 422


async def test_turn_alternates():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    game = await store.place(1, 1, 0, 0)
    assert game.turn == O
    game = await store.place(1, 2, 1, 1)
    assert game.turn == X


async def test_full_game_x_wins_horizontally():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.place(1, 1, 0, 0)  # X
    await store.place(1, 2, 1, 0)  # O
    await store.place(1, 1, 0, 1)  # X
    await store.place(1, 2, 1, 1)  # O
    game = await store.place(1, 1, 0, 2)  # X, 0행 완성 (3목)
    assert game.status == "finished"
    assert game.winner_user_id == 1
    assert game.winning_line is not None and len(game.winning_line) == 3


async def test_draw_when_board_fills_without_winner():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")  # X
    await store.join(1, 2, "Bob")  # O
    # 어느 줄도 3목이 완성되지 않는 순서로 판을 가득 채운다.
    # X: (0,0)(1,0)(0,2)(2,1)(2,2)  O: (0,1)(1,1)(1,2)(2,0)
    moves = [
        (1, 0, 0),
        (2, 0, 1),
        (1, 1, 0),
        (2, 1, 1),
        (1, 0, 2),
        (2, 1, 2),
        (1, 2, 1),
        (2, 2, 0),
    ]
    for user_id, row, col in moves:
        await store.place(1, user_id, row, col)
    result = await store.place(1, 1, 2, 2)
    assert result.status == "finished"
    assert result.winner_user_id is None
    assert result.winning_line is None


async def test_reset_clears_players_and_board():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.place(1, 1, 0, 0)
    game = await store.reset(1)
    # reset()은 join()의 "판만 비우기"와 달리 완전히 새 게임을 만든다 — 참가자도
    # 전부 사라진다. 통합 시 이 차이(reset vs 재대국 join)가 사라지지 않게 고정.
    assert game.status == "waiting"
    assert game.players == []
    assert all(cell == EMPTY for row in game.board for cell in row)


async def test_join_after_finish_opens_new_round_keeping_players():
    store = TicTacToeStore()
    await store.join(1, 1, "Alice")
    await store.join(1, 2, "Bob")
    await store.place(1, 1, 0, 0)
    await store.place(1, 2, 1, 0)
    await store.place(1, 1, 0, 1)
    await store.place(1, 2, 1, 1)
    finished = await store.place(1, 1, 0, 2)
    assert finished.status == "finished"

    game = await store.join(1, 1, "Alice")
    assert game.status == "playing"
    assert game.winner_user_id is None
    assert all(cell == EMPTY for row in game.board for cell in row)
    assert game.turn == X
    assert len(game.players) == 2  # 재대국은 join()과 달리 참가자가 그대로 남는다


# ---------- API 플로우 ----------


@pytest.fixture
def fresh_tictactoe_store():
    from app.main import app

    store = TicTacToeStore()
    app.dependency_overrides[get_tictactoe_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_tictactoe_store, None)


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


async def test_api_full_flow(client: AsyncClient, register, fresh_tictactoe_store):
    token_a, token_b, channel_id = await _setup_channel(client, register)

    joined = await client.post(
        f"/channels/{channel_id}/tictactoe/join", headers=_headers(token_a)
    )
    assert joined.status_code == 200
    assert joined.json()["status"] == "waiting"

    started = await client.post(
        f"/channels/{channel_id}/tictactoe/join", headers=_headers(token_b)
    )
    assert started.status_code == 200
    assert started.json()["status"] == "playing"
    assert started.json()["turn_user_id"] is not None

    placed = await client.post(
        f"/channels/{channel_id}/tictactoe/place",
        json={"row": 1, "col": 1},
        headers=_headers(token_a),
    )
    assert placed.status_code == 200
    assert placed.json()["board"][1][1] == X

    # O 차례인데 X가 또 두면 409
    again = await client.post(
        f"/channels/{channel_id}/tictactoe/place",
        json={"row": 0, "col": 0},
        headers=_headers(token_a),
    )
    assert again.status_code == 409

    fetched = await client.get(f"/channels/{channel_id}/tictactoe", headers=_headers(token_b))
    assert fetched.status_code == 200
    assert fetched.json()["board"][1][1] == X


async def test_api_non_member_403(client: AsyncClient, register, fresh_tictactoe_store):
    _token_a, _token_b, channel_id = await _setup_channel(client, register)
    token_c = await register(client, "c@test.com", "pass1234", "Carol")
    resp = await client.post(f"/channels/{channel_id}/tictactoe/join", headers=_headers(token_c))
    assert resp.status_code == 403
