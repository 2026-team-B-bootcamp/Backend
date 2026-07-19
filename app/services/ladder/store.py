"""사다리타기 게임 세션의 참가자/결과/실행 상태를 메모리에 저장하는 저장소.

routers/ladder.py의 각 엔드포인트가 호출하는 진입점이며, 사다리 생성과 경로 계산
자체는 logic.py에 위임한다. 상태는 대기(waiting) → 결과공개(revealed)로 전이한다.
"""

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, status

from app.services.ladder.logic import compute_assignment, generate_rungs

TTL_SECONDS = 3600
MAX_ENTRIES = 8
MAX_LABEL_LENGTH = 20

WAITING = "waiting"
REVEALED = "revealed"


@dataclass
class LadderEntryRecord:
    id: int
    label: str
    added_by: str


@dataclass
class LadderGame:
    channel_id: int
    status: str = WAITING
    participants: list[LadderEntryRecord] = field(default_factory=list)
    results: list[LadderEntryRecord] = field(default_factory=list)
    next_id: int = 1
    rungs: list[list[bool]] | None = None
    assignment: list[int] | None = None
    run_by: str | None = None
    last_touched: float = 0.0


def _require_waiting(game: LadderGame) -> None:
    if game.status != WAITING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 결과가 나왔어요. 새 라운드를 시작하세요",
        )


class LadderStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._games: dict[int, LadderGame] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds
        self._clock = clock
        self._rng = rng or random.Random()

    def _sweep(self) -> None:
        now = self._clock()
        expired = [cid for cid, g in self._games.items() if now - g.last_touched > self._ttl]
        for cid in expired:
            del self._games[cid]

    def _get_or_create(self, channel_id: int) -> LadderGame:
        game = self._games.get(channel_id)
        if game is None:
            game = LadderGame(channel_id=channel_id, last_touched=self._clock())
            self._games[channel_id] = game
        return game

    async def get_or_create(self, channel_id: int) -> LadderGame:
        async with self._lock:
            self._sweep()
            game = self._get_or_create(channel_id)
            game.last_touched = self._clock()
            return game

    def _add_entry(
        self, bucket: list[LadderEntryRecord], game: LadderGame, label: str, added_by: str
    ) -> None:
        label = label.strip()
        if not label or len(label) > MAX_LABEL_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"항목은 1~{MAX_LABEL_LENGTH}자로 입력하세요",
            )
        if len(bucket) >= MAX_ENTRIES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"항목은 최대 {MAX_ENTRIES}개까지 추가할 수 있어요",
            )
        bucket.append(LadderEntryRecord(id=game.next_id, label=label, added_by=added_by))
        game.next_id += 1

    async def add_participant(self, channel_id: int, label: str, added_by: str) -> LadderGame:
        async with self._lock:
            self._sweep()
            game = self._get_or_create(channel_id)
            _require_waiting(game)
            self._add_entry(game.participants, game, label, added_by)
            game.last_touched = self._clock()
            return game

    async def add_result(self, channel_id: int, label: str, added_by: str) -> LadderGame:
        async with self._lock:
            self._sweep()
            game = self._get_or_create(channel_id)
            _require_waiting(game)
            self._add_entry(game.results, game, label, added_by)
            game.last_touched = self._clock()
            return game

    async def remove_participant(self, channel_id: int, entry_id: int) -> LadderGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 사다리가 없어요"
                )
            _require_waiting(game)
            game.participants = [p for p in game.participants if p.id != entry_id]
            game.last_touched = self._clock()
            return game

    async def remove_result(self, channel_id: int, entry_id: int) -> LadderGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 사다리가 없어요"
                )
            _require_waiting(game)
            game.results = [r for r in game.results if r.id != entry_id]
            game.last_touched = self._clock()
            return game

    async def run(self, channel_id: int, run_by: str) -> LadderGame:
        async with self._lock:
            self._sweep()
            game = self._games.get(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 사다리가 없어요"
                )
            _require_waiting(game)
            columns = len(game.participants)
            if columns < 2 or len(game.results) != columns:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="참가자와 결과가 2개 이상, 같은 개수로 있어야 해요",
                )
            # 사다리 모양을 무작위로 만들고(rungs), 각 참가자가 도착하는 결과 인덱스를 계산한다.
            rungs = generate_rungs(columns, rng=self._rng)
            game.rungs = rungs
            game.assignment = compute_assignment(rungs, columns)
            game.status = REVEALED
            game.run_by = run_by
            game.last_touched = self._clock()
            return game

    async def reset(self, channel_id: int) -> LadderGame:
        async with self._lock:
            self._sweep()
            game = self._get_or_create(channel_id)
            game.status = WAITING
            game.participants = []
            game.results = []
            game.next_id = 1
            game.rungs = None
            game.assignment = None
            game.run_by = None
            game.last_touched = self._clock()
            return game

    async def get(self, channel_id: int) -> LadderGame | None:
        async with self._lock:
            self._sweep()
            return self._games.get(channel_id)


store = LadderStore()


def get_ladder_store() -> LadderStore:
    return store
