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
                # Someone re-joining after a win starts a fresh game.
                game.called_numbers = set()
                game.players = {}
                game.winner_user_id = None
            game.last_touched = self._clock()
            if user_id not in game.players:
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
            game.called_numbers.add(number)

            completed = {
                pid: count_completed_lines(player.board, game.called_numbers)
                for pid, player in game.players.items()
            }
            if game.winner_user_id is None:
                qualified = [pid for pid, lines in completed.items() if lines >= WIN_LINES]
                if qualified:
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
