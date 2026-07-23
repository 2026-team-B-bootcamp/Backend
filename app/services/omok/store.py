"""채널별 오목 게임 상태를 Redis에 저장하고 join/place(착수)/reset을 처리한다.

라우터(routers/omok.py)가 이 store를 호출해 대국을 열고 진행시킨다. 착수 시
승리(5목) 판정은 이 파일의 find_winning_line이 담당하고, 통과하면 store가
턴을 넘기거나 게임을 종료 상태로 바꾼다.

저장 방식: 게임 하나를 JSON으로 직렬화해 "game:omok:{채널id}" 키에 TTL과 함께
저장한다. 방치된 게임은 Redis가 TTL 만료로 알아서 지우므로 별도의 청소 로직이
필요 없고, 워커가 여러 개여도 모두 같은 상태를 본다. 동시 요청은 채널별 Redis
분산 락으로 직렬화한다 (이전의 asyncio.Lock은 한 프로세스 안에서만 유효했다).
"""

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis
from app.services.game_ttl import ttl_for

TTL_SECONDS = 3600
BOARD_SIZE = 15
WIN_LENGTH = 5

EMPTY = 0
BLACK = 1
WHITE = 2

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"

_DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]


def _empty_board() -> list[list[int]]:
    return [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def find_winning_line(
    board: list[list[int]], row: int, col: int, color: int
) -> list[list[int]] | None:
    """방금 둔 (row, col) 돌을 지나는 5목 이상 줄이 있으면 그 좌표들을 돌려준다."""
    # 가로/세로/대각선 두 방향, 총 4개 축(_DIRECTIONS)에 대해 방금 둔 자리를
    # 기준으로 양쪽으로 같은 색 돌이 몇 개 이어지는지 센다.
    for dr, dc in _DIRECTIONS:
        cells = [(row, col)]
        r, c = row + dr, col + dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
            cells.append((r, c))
            r += dr
            c += dc
        r, c = row - dr, col - dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
            cells.insert(0, (r, c))
            r -= dr
            c -= dc
        # 한 축에서 이어진 돌이 5개(WIN_LENGTH) 이상이면 그 줄을 승리 라인으로 반환.
        if len(cells) >= WIN_LENGTH:
            return [[r, c] for r, c in cells]
    return None


@dataclass
class OmokPlayer:
    user_id: int
    display_name: str
    color: int


@dataclass
class OmokGame:
    channel_id: int
    status: str = WAITING
    players: list[OmokPlayer] = field(default_factory=list)
    board: list[list[int]] = field(default_factory=_empty_board)
    turn: int = BLACK
    winner_user_id: int | None = None
    winning_line: list[list[int]] | None = None
    last_move: list[int] | None = None
    move_count: int = 0

    def find_player(self, user_id: int) -> OmokPlayer | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def player_by_color(self, color: int) -> OmokPlayer | None:
        return next((p for p in self.players if p.color == color), None)

    def current_player(self) -> OmokPlayer | None:
        if self.status != PLAYING:
            return None
        return self.player_by_color(self.turn)

    def _reset_board(self) -> None:
        self.board = _empty_board()
        self.turn = BLACK
        self.winner_user_id = None
        self.winning_line = None
        self.last_move = None
        self.move_count = 0


def _to_json(game: OmokGame) -> str:
    return json.dumps(asdict(game))


def _from_json(raw: str) -> OmokGame:
    data = json.loads(raw)
    data["players"] = [OmokPlayer(**p) for p in data["players"]]
    return OmokGame(**data)


class OmokStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock

    def _key(self, channel_id: int) -> str:
        return f"game:omok:{channel_id}"

    def _lock(self, channel_id: int):
        # 같은 채널에 대한 동시 요청을 워커에 상관없이 한 줄로 세우는 분산 락.
        return get_redis().lock(f"lock:omok:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> OmokGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return _from_json(raw) if raw else None

    async def _save(self, game: OmokGame) -> None:
        # 저장할 때마다 TTL을 다시 걸어준다 — 활동이 있는 게임은 계속 살아 있고,
        # 방치된 게임은 TTL 만료로 Redis에서 자동 소멸한다.
        await get_redis().set(
            self._key(game.channel_id),
            _to_json(game),
            # 대기·종료 상태로 방치되면 30초 뒤 자동으로 사라진다(game_ttl 참고).
            ex=ttl_for(game.status, self._ttl),
        )

    async def join(self, channel_id: int, user_id: int, display_name: str) -> OmokGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                game = OmokGame(channel_id=channel_id)

            if game.status == FINISHED:
                # 끝난 판에 다시 들어오면 판을 비우고 새 라운드를 연다.
                game._reset_board()
                game.status = PLAYING if len(game.players) >= 2 else WAITING

            if game.find_player(user_id) is None:
                if len(game.players) >= 2:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="이미 두 명이 대국 중이에요. 관전만 할 수 있어요",
                    )

                # 먼저 들어온 사람이 흑(선공), 두 번째가 백이 된다.
                color = BLACK if not game.players else WHITE
                game.players.append(
                    OmokPlayer(user_id=user_id, display_name=display_name, color=color)
                )
                if len(game.players) == 2:
                    # 두 명이 모이면 바로 대국 시작, 흑이 먼저 둔다.
                    game.status = PLAYING
                    game.turn = BLACK

            await self._save(game)
            return game

    async def place(self, channel_id: int, user_id: int, row: int, col: int) -> OmokGame:
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
            if player.color != game.turn:
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
                    status_code=status.HTTP_409_CONFLICT, detail="이미 돌이 놓인 자리예요"
                )

            game.board[row][col] = player.color
            game.last_move = [row, col]
            game.move_count += 1

            # 방금 둔 자리를 기준으로 5목이 완성됐는지 확인한다.
            line = find_winning_line(game.board, row, col, player.color)
            if line is not None:
                game.status = FINISHED
                game.winner_user_id = player.user_id
                game.winning_line = line
            elif game.move_count >= BOARD_SIZE * BOARD_SIZE:
                # 판이 꽉 찼는데 승자가 없으면 무승부로 종료.
                game.status = FINISHED
                game.winner_user_id = None
            else:
                # 승부가 안 났으면 턴을 상대에게 넘긴다.
                game.turn = WHITE if game.turn == BLACK else BLACK

            await self._save(game)
            return game

    async def reset(self, channel_id: int) -> OmokGame:
        async with self._lock(channel_id):
            game = OmokGame(channel_id=channel_id)
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> OmokGame | None:
        return await self._load(channel_id)

    async def status(self, channel_id: int) -> str:
        game = await self._load(channel_id)
        return game.status if game else "none"


store = OmokStore()


def get_omok_store() -> OmokStore:
    return store
