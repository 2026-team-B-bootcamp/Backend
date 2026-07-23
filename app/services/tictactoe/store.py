"""채널별 틱택토(3×3, 3목) 게임 상태를 Redis에 저장하고 join/place/reset을 처리한다.

오목(services/omok)과 사실상 같은 구조다 — 판 크기(3)와 승리 길이(3)만 다르다.
저장 방식: 게임 하나를 JSON으로 직렬화해 "game:tictactoe:{채널id}" 키에 TTL과 함께
저장하고, 채널별 Redis 분산 락으로 동시 요청을 직렬화한다.
"""

import json
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis

TTL_SECONDS = 3600
BOARD_SIZE = 3
WIN_LENGTH = 3

EMPTY = 0
X = 1  # 선공
O = 2  # noqa: E741 — 마크 상수(오목의 BLACK/WHITE와 대응)

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"

_DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]


def _empty_board() -> list[list[int]]:
    return [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def find_winning_line(
    board: list[list[int]], row: int, col: int, mark: int
) -> list[list[int]] | None:
    """방금 둔 (row, col)을 지나는 3목 줄이 있으면 그 좌표들을 돌려준다."""
    for dr, dc in _DIRECTIONS:
        cells = [(row, col)]
        r, c = row + dr, col + dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == mark:
            cells.append((r, c))
            r += dr
            c += dc
        r, c = row - dr, col - dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == mark:
            cells.insert(0, (r, c))
            r -= dr
            c -= dc
        if len(cells) >= WIN_LENGTH:
            return [[r, c] for r, c in cells]
    return None


@dataclass
class TicTacToePlayer:
    user_id: int
    display_name: str
    mark: int


@dataclass
class TicTacToeGame:
    channel_id: int
    status: str = WAITING
    players: list[TicTacToePlayer] = field(default_factory=list)
    board: list[list[int]] = field(default_factory=_empty_board)
    turn: int = X
    winner_user_id: int | None = None
    winning_line: list[list[int]] | None = None
    last_move: list[int] | None = None
    move_count: int = 0

    def find_player(self, user_id: int) -> TicTacToePlayer | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def player_by_mark(self, mark: int) -> TicTacToePlayer | None:
        return next((p for p in self.players if p.mark == mark), None)

    def current_player(self) -> TicTacToePlayer | None:
        if self.status != PLAYING:
            return None
        return self.player_by_mark(self.turn)

    def _reset_board(self) -> None:
        self.board = _empty_board()
        self.turn = X
        self.winner_user_id = None
        self.winning_line = None
        self.last_move = None
        self.move_count = 0


def _to_json(game: TicTacToeGame) -> str:
    return json.dumps(asdict(game))


def _from_json(raw: str) -> TicTacToeGame:
    data = json.loads(raw)
    data["players"] = [TicTacToePlayer(**p) for p in data["players"]]
    return TicTacToeGame(**data)


class TicTacToeStore:
    def __init__(self, ttl_seconds: float = TTL_SECONDS) -> None:
        self._ttl = ttl_seconds

    def _key(self, channel_id: int) -> str:
        return f"game:tictactoe:{channel_id}"

    def _lock(self, channel_id: int):
        return get_redis().lock(f"lock:tictactoe:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> TicTacToeGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return _from_json(raw) if raw else None

    async def _save(self, game: TicTacToeGame) -> None:
        await get_redis().set(self._key(game.channel_id), _to_json(game), ex=int(self._ttl))

    async def join(self, channel_id: int, user_id: int, display_name: str) -> TicTacToeGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                game = TicTacToeGame(channel_id=channel_id)

            if game.status == FINISHED:
                game._reset_board()
                game.status = PLAYING if len(game.players) >= 2 else WAITING

            if game.find_player(user_id) is None:
                if len(game.players) >= 2:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="이미 두 명이 대국 중이에요. 관전만 할 수 있어요",
                    )
                # 먼저 들어온 사람이 X(선공), 두 번째가 O.
                mark = X if not game.players else O
                game.players.append(
                    TicTacToePlayer(user_id=user_id, display_name=display_name, mark=mark)
                )
                if len(game.players) == 2:
                    game.status = PLAYING
                    game.turn = X

            await self._save(game)
            return game

    async def place(self, channel_id: int, user_id: int, row: int, col: int) -> TicTacToeGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="진행 중인 게임이 없어요"
                )
            if game.status != PLAYING:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="진행 중인 게임이 아니에요"
                )
            player = game.find_player(user_id)
            if player is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="이 대국의 플레이어가 아니에요"
                )
            if player.mark != game.turn:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="지금은 당신 차례가 아니에요"
                )
            if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="판 밖에는 둘 수 없어요",
                )
            if game.board[row][col] != EMPTY:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="이미 표시된 칸이에요"
                )

            game.board[row][col] = player.mark
            game.last_move = [row, col]
            game.move_count += 1

            line = find_winning_line(game.board, row, col, player.mark)
            if line is not None:
                game.status = FINISHED
                game.winner_user_id = player.user_id
                game.winning_line = line
            elif game.move_count >= BOARD_SIZE * BOARD_SIZE:
                game.status = FINISHED
                game.winner_user_id = None
            else:
                game.turn = O if game.turn == X else X

            await self._save(game)
            return game

    async def reset(self, channel_id: int) -> TicTacToeGame:
        async with self._lock(channel_id):
            game = TicTacToeGame(channel_id=channel_id)
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> TicTacToeGame | None:
        return await self._load(channel_id)

    async def status(self, channel_id: int) -> str:
        game = await self._load(channel_id)
        return game.status if game else "none"


store = TicTacToeStore()


def get_tictactoe_store() -> TicTacToeStore:
    return store
