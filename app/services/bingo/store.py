"""채널별 빙고 게임 상태를 메모리에 저장하고 join/click(번호 호출)을 처리한다.

라우터(routers/bingo.py)가 이 store를 호출해 게임을 열고 진행시키며,
승리 판정은 services/bingo/logic.py의 count_completed_lines에 위임한다.
오래 방치된 채널의 게임은 TTL이 지나면 _sweep에서 자동으로 정리된다.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, status

from app.services.bingo.logic import WIN_LINES, count_completed_lines, generate_board

TTL_SECONDS = 3600


@dataclass
class BingoPlayer:
    user_id: int
    display_name: str
    board: list[int]


@dataclass
class BingoGame:
    channel_id: int
    called_numbers: set[int] = field(default_factory=set)
    players: dict[int, BingoPlayer] = field(default_factory=dict)
    winner_user_id: int | None = None
    # 몇 번째 판인지. 승자가 나온 뒤 누군가 다시 join 하면 1씩 올라간다.
    round: int = 1
    last_touched: float = 0.0


class BingoGameStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._games: dict[int, BingoGame] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds
        self._clock = clock

    def _sweep(self) -> None:
        # 마지막 활동으로부터 TTL(1시간)이 지난 채널의 게임은 메모리에서 지운다.
        now = self._clock()
        expired = [
            cid for cid, g in self._games.items() if now - g.last_touched > self._ttl
        ]
        for cid in expired:
            del self._games[cid]

    async def join(
        self, channel_id: int, user_id: int, display_name: str
    ) -> BingoGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None:
                game = BingoGame(channel_id=channel_id, last_touched=self._clock())
                self._games[channel_id] = game
            elif game.winner_user_id is not None:
                # Someone re-joining after a win starts a fresh game (new round).
                game.called_numbers = set()
                game.players = {}
                game.winner_user_id = None
                game.round += 1
            game.last_touched = self._clock()
            if user_id not in game.players:
                # 처음 참가하는 유저에게만 새 보드를 발급한다 (재접속 시 기존 보드 유지).
                game.players[user_id] = BingoPlayer(
                    user_id=user_id, display_name=display_name, board=generate_board()
                )
            return game

    async def click(self, channel_id: int, user_id: int, number: int) -> BingoGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None or user_id not in game.players:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Not a player in this game",
                )
            game.last_touched = self._clock()
            # 번호를 호출 목록에 추가하면, 이 번호를 가진 모든 플레이어의 보드에
            # 자동으로 반영된다(마킹 여부는 called_numbers와의 비교로 계산되므로).
            game.called_numbers.add(number)

            # 아직 승자가 없다면, 모든 플레이어의 완성 줄 수를 다시 계산해
            # WIN_LINES(3줄) 이상 달성한 사람이 있는지 확인한다.
            completed = {
                pid: count_completed_lines(player.board, game.called_numbers)
                for pid, player in game.players.items()
            }
            if game.winner_user_id is None:
                qualified = [pid for pid, lines in completed.items() if lines >= WIN_LINES]
                if qualified:
                    # 방금 클릭한 사람이 동시에 조건을 채웠다면 그 사람을 우선한다.
                    game.winner_user_id = (
                        user_id if user_id in qualified else qualified[0]
                    )
            return game

    async def get(self, channel_id: int) -> BingoGame | None:
        async with self._lock:
            self._sweep()
            return self._games.get(channel_id)


store = BingoGameStore()


def get_bingo_store() -> BingoGameStore:
    return store
