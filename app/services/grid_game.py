"""오목·틱택토처럼 "N×N 판에 번갈아 두다가 K개를 연속으로 놓으면 승리"인
격자 게임들이 공유하는 저장소·판정 로직.

오목(15×15, 5목)과 틱택토(3×3, 3목)는 판 크기와 승리에 필요한 연속 개수만
다를 뿐 나머지(승리 판정 알고리즘, join/place/reset 흐름, Redis 저장 방식)가
완전히 같다. 이 모듈이 그 공통 부분을 갖고, app/services/omok, app/services/
tictactoe는 각자의 상수(판 크기, 승리 길이, 진영 표시자, 점유 칸 에러 문구)만
정의해 GridGameStore를 구성하는 얇은 래퍼로 남는다.

저장 방식: 게임 하나를 JSON으로 직렬화해 "game:{kind}:{채널id}" 키에 TTL과 함께
저장한다. 방치된 게임은 Redis가 TTL 만료로 알아서 지우므로 별도의 청소 로직이
필요 없고, 워커가 여러 개여도 모두 같은 상태를 본다. 동시 요청은 채널별 Redis
분산 락으로 직렬화한다 (이전의 asyncio.Lock은 한 프로세스 안에서만 유효했다).
"""

import json
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis
from app.services.game_ttl import ttl_for

TTL_SECONDS = 3600

EMPTY = 0

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"

_DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]


def find_winning_line(
    board: list[list[int]], row: int, col: int, mark: int, board_size: int, win_length: int
) -> list[list[int]] | None:
    """방금 둔 (row, col)을 지나는 win_length 이상 연속 줄이 있으면 그 좌표들을 돌려준다."""
    # 가로/세로/대각선 두 방향, 총 4개 축(_DIRECTIONS)에 대해 방금 둔 자리를
    # 기준으로 양쪽으로 같은 표시(mark)가 몇 개 이어지는지 센다.
    for dr, dc in _DIRECTIONS:
        cells = [(row, col)]
        r, c = row + dr, col + dc
        while 0 <= r < board_size and 0 <= c < board_size and board[r][c] == mark:
            cells.append((r, c))
            r += dr
            c += dc
        r, c = row - dr, col - dc
        while 0 <= r < board_size and 0 <= c < board_size and board[r][c] == mark:
            cells.insert(0, (r, c))
            r -= dr
            c -= dc
        # 한 축에서 이어진 표시가 win_length개 이상이면 그 줄을 승리 라인으로 반환.
        if len(cells) >= win_length:
            return [[r, c] for r, c in cells]
    return None


@dataclass
class GridGamePlayer:
    user_id: int
    display_name: str
    mark: int


@dataclass
class GridGame:
    channel_id: int
    # 판 크기·선공 표시자는 게임 종류마다 달라 정적 기본값을 줄 수 없다 —
    # 항상 GridGameStore._new_game()이 명시적으로 채워서 생성한다.
    board: list[list[int]]
    turn: int
    status: str = WAITING
    # 이 판을 연 사람(방장). 강제 종료 권한의 기준이며, 새 라운드가 열릴 때마다
    # 그 라운드를 다시 연 사람으로 바뀐다 (game_host.py 참고).
    host_user_id: int | None = None
    players: list[GridGamePlayer] = field(default_factory=list)
    winner_user_id: int | None = None
    winning_line: list[list[int]] | None = None
    last_move: list[int] | None = None
    move_count: int = 0

    def find_player(self, user_id: int) -> GridGamePlayer | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def player_by_mark(self, mark: int) -> GridGamePlayer | None:
        return next((p for p in self.players if p.mark == mark), None)

    def current_player(self) -> GridGamePlayer | None:
        if self.status != PLAYING:
            return None
        return self.player_by_mark(self.turn)


class GridGameStore:
    """판 크기·승리 길이·선공/후공 표시자·점유 칸 에러 문구만 다른 격자 게임의
    공통 저장소. 서브클래스(OmokStore/TicTacToeStore)는 그 값들만 넘기고,
    자기 게임만의 속성(예: 오목의 color)을 노출하려면 game_cls/player_cls로
    Game/Player 서브클래스를 지정한다.
    """

    game_cls: type[GridGame] = GridGame
    player_cls: type[GridGamePlayer] = GridGamePlayer

    def __init__(
        self,
        kind: str,
        board_size: int,
        win_length: int,
        first_mark: int,
        second_mark: int,
        occupied_message: str,
        ttl_seconds: float = TTL_SECONDS,
    ) -> None:
        self._kind = kind
        self._board_size = board_size
        self._win_length = win_length
        self._first_mark = first_mark
        self._second_mark = second_mark
        self._occupied_message = occupied_message
        self._ttl = ttl_seconds

    def _empty_board(self) -> list[list[int]]:
        return [[EMPTY] * self._board_size for _ in range(self._board_size)]

    def _new_game(self, channel_id: int) -> GridGame:
        return self.game_cls(
            channel_id=channel_id, board=self._empty_board(), turn=self._first_mark
        )

    def _reset_board(self, game: GridGame) -> None:
        game.board = self._empty_board()
        game.turn = self._first_mark
        game.winner_user_id = None
        game.winning_line = None
        game.last_move = None
        game.move_count = 0

    def _key(self, channel_id: int) -> str:
        return f"game:{self._kind}:{channel_id}"

    def _lock(self, channel_id: int):
        # 같은 채널에 대한 동시 요청을 워커에 상관없이 한 줄로 세우는 분산 락.
        return get_redis().lock(f"lock:{self._kind}:{channel_id}", timeout=10)

    def _to_json(self, game: GridGame) -> str:
        return json.dumps(asdict(game))

    def _migrate_player(self, p: dict) -> dict:
        """옛 포맷으로 저장된 플레이어 dict를 현재 필드명으로 옮긴다.

        기본은 그대로 두고, 필드명이 바뀐 게임만 서브클래스에서 덮어쓴다.
        """
        return p

    def _from_json(self, raw: str) -> GridGame:
        data = json.loads(raw)
        data["players"] = [self.player_cls(**self._migrate_player(p)) for p in data["players"]]
        # 방장 도입 이전에 저장된 판에는 이 키가 없다 — 없으면 방장 없는 판으로 둔다.
        data.setdefault("host_user_id", None)
        return self.game_cls(**data)

    async def _load(self, channel_id: int) -> GridGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return self._from_json(raw) if raw else None

    async def _save(self, game: GridGame) -> None:
        # 저장할 때마다 TTL을 다시 걸어준다 — 활동이 있는 게임은 계속 살아 있고,
        # 방치된 게임은 TTL 만료로 Redis에서 자동 소멸한다.
        await get_redis().set(
            self._key(game.channel_id),
            self._to_json(game),
            # 대기·종료 상태로 방치되면 30초 뒤 자동으로 사라진다(game_ttl 참고).
            ex=ttl_for(game.status, self._ttl),
        )

    async def join(self, channel_id: int, user_id: int, display_name: str) -> GridGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                game = self._new_game(channel_id)

            if game.status == FINISHED:
                # 끝난 판에 다시 들어오면 판을 비우고 새 라운드를 연다.
                self._reset_board(game)
                game.status = PLAYING if len(game.players) >= 2 else WAITING

            # 재대국에서도 두 사람이 그대로 남으므로 방장은 처음 판을 연 사람으로 유지된다.
            if game.host_user_id is None:
                game.host_user_id = user_id

            if game.find_player(user_id) is None:
                if len(game.players) >= 2:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="이미 두 명이 대국 중이에요. 관전만 할 수 있어요",
                    )

                # 먼저 들어온 사람이 선공, 두 번째가 후공이 된다.
                mark = self._first_mark if not game.players else self._second_mark
                game.players.append(
                    self.player_cls(user_id=user_id, display_name=display_name, mark=mark)
                )
                if len(game.players) == 2:
                    # 두 명이 모이면 바로 대국 시작, 선공이 먼저 둔다.
                    game.status = PLAYING
                    game.turn = self._first_mark

            await self._save(game)
            return game

    async def place(self, channel_id: int, user_id: int, row: int, col: int) -> GridGame:
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
            if not (0 <= row < self._board_size and 0 <= col < self._board_size):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="판 밖에는 둘 수 없어요",
                )
            if game.board[row][col] != EMPTY:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=self._occupied_message
                )

            game.board[row][col] = player.mark
            game.last_move = [row, col]
            game.move_count += 1

            # 방금 둔 자리를 기준으로 승리 조건이 완성됐는지 확인한다.
            line = find_winning_line(
                game.board, row, col, player.mark, self._board_size, self._win_length
            )
            if line is not None:
                game.status = FINISHED
                game.winner_user_id = player.user_id
                game.winning_line = line
            elif game.move_count >= self._board_size * self._board_size:
                # 판이 꽉 찼는데 승자가 없으면 무승부로 종료.
                game.status = FINISHED
                game.winner_user_id = None
            else:
                # 승부가 안 났으면 턴을 상대에게 넘긴다.
                game.turn = (
                    self._second_mark if game.turn == self._first_mark else self._first_mark
                )

            await self._save(game)
            return game

    async def reset(self, channel_id: int) -> GridGame:
        async with self._lock(channel_id):
            game = self._new_game(channel_id)
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> GridGame | None:
        return await self._load(channel_id)

    async def status(self, channel_id: int) -> str:
        game = await self._load(channel_id)
        return game.status if game else "none"

    async def host(self, channel_id: int) -> int | None:
        game = await self._load(channel_id)
        return game.host_user_id if game else None

    async def clear(self, channel_id: int) -> None:
        """판을 통째로 지운다 — 방장의 강제 종료용(games 라우터가 호출)."""
        await get_redis().delete(self._key(channel_id))
