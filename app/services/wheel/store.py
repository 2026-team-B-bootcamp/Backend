"""돌림판(룰렛) 게임 세션의 항목/추첨 결과를 메모리에 저장하는 저장소.

routers/wheel.py의 각 엔드포인트가 호출하는 진입점이다. 별도의 logic 모듈 없이
추첨 로직(무작위 선택)은 spin()에서 바로 처리한다.
"""

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, status

TTL_SECONDS = 3600
MAX_OPTIONS = 12
MAX_LABEL_LENGTH = 20


@dataclass
class WheelOptionEntry:
    id: int
    label: str
    added_by: str


@dataclass
class WheelGame:
    channel_id: int
    options: list[WheelOptionEntry] = field(default_factory=list)
    next_option_id: int = 1
    result_option_id: int | None = None
    spun_by: str | None = None
    last_touched: float = 0.0


class WheelStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._games: dict[int, WheelGame] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds
        self._clock = clock
        self._rng = rng or random.Random()

    def _sweep(self) -> None:
        now = self._clock()
        expired = [cid for cid, g in self._games.items() if now - g.last_touched > self._ttl]
        for cid in expired:
            del self._games[cid]

    def _get_or_create(self, channel_id: int) -> WheelGame:
        game = self._games.get(channel_id)
        if game is None:
            game = WheelGame(channel_id=channel_id, last_touched=self._clock())
            self._games[channel_id] = game
        return game

    async def get_or_create(self, channel_id: int) -> WheelGame:
        async with self._lock:
            self._sweep()
            game = self._get_or_create(channel_id)
            game.last_touched = self._clock()
            return game

    async def add_option(self, channel_id: int, label: str, added_by: str) -> WheelGame:
        label = label.strip()
        if not label or len(label) > MAX_LABEL_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"항목은 1~{MAX_LABEL_LENGTH}자로 입력하세요",
            )
        async with self._lock:
            self._sweep()
            game = self._get_or_create(channel_id)
            if game.result_option_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="이번 라운드는 이미 돌렸어요. 새 라운드를 시작하세요",
                )
            if len(game.options) >= MAX_OPTIONS:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"항목은 최대 {MAX_OPTIONS}개까지 추가할 수 있어요",
                )
            game.options.append(
                WheelOptionEntry(id=game.next_option_id, label=label, added_by=added_by)
            )
            game.next_option_id += 1
            game.last_touched = self._clock()
            return game

    async def remove_option(self, channel_id: int, option_id: int) -> WheelGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 돌림판이 없어요"
                )
            if game.result_option_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="이번 라운드는 이미 돌렸어요. 새 라운드를 시작하세요",
                )
            game.options = [o for o in game.options if o.id != option_id]
            game.last_touched = self._clock()
            return game

    async def spin(self, channel_id: int, spun_by: str) -> WheelGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None or len(game.options) < 2:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="항목이 2개 이상 있어야 돌릴 수 있어요",
                )
            if game.result_option_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="이번 라운드는 이미 돌렸어요. 새 라운드를 시작하세요",
                )
            # 등록된 항목 중 하나를 무작위로 뽑아 이번 라운드 결과로 고정한다.
            winner = self._rng.choice(game.options)
            game.result_option_id = winner.id
            game.spun_by = spun_by
            game.last_touched = self._clock()
            return game

    async def reset(self, channel_id: int) -> WheelGame:
        async with self._lock:
            self._sweep()
            game = self._get_or_create(channel_id)
            game.options = []
            game.next_option_id = 1
            game.result_option_id = None
            game.spun_by = None
            game.last_touched = self._clock()
            return game

    async def get(self, channel_id: int) -> WheelGame | None:
        async with self._lock:
            self._sweep()
            return self._games.get(channel_id)


store = WheelStore()


def get_wheel_store() -> WheelStore:
    return store
