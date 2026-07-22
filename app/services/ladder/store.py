"""사다리타기 게임 세션의 참가자/결과/실행 상태를 Redis에 저장하고 전이시키는 저장소.

routers/ladder.py의 각 엔드포인트가 호출하는 진입점이며, 사다리 생성과 경로 계산
자체는 logic.py에 위임한다. 상태는 대기(waiting) → 결과공개(revealed)로 전이한다.

저장 방식: 게임 하나를 JSON으로 직렬화해 "game:ladder:{채널id}" 키에 TTL과
함께 저장한다. 방치된 게임은 Redis가 TTL 만료로 알아서 지우므로 별도의 청소
로직이 필요 없고, 워커가 여러 개여도 모두 같은 상태를 본다. 동시 요청은
채널별 Redis 분산 락으로 직렬화한다 (이전의 asyncio.Lock은 한 프로세스 안에서만
유효했다).
"""

import json
import random
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis
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


def _require_waiting(game: LadderGame) -> None:
    if game.status != WAITING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 결과가 나왔어요. 새 라운드를 시작하세요",
        )


def _to_json(game: LadderGame) -> str:
    return json.dumps(asdict(game))


def _from_json(raw: str) -> LadderGame:
    data = json.loads(raw)
    data["participants"] = [LadderEntryRecord(**p) for p in data["participants"]]
    data["results"] = [LadderEntryRecord(**r) for r in data["results"]]
    return LadderGame(**data)


class LadderStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        rng: random.Random | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._rng = rng or random.Random()

    def _key(self, channel_id: int) -> str:
        return f"game:ladder:{channel_id}"

    def _lock(self, channel_id: int):
        # 같은 채널에 대한 동시 요청을 워커에 상관없이 한 줄로 세우는 분산 락.
        return get_redis().lock(f"lock:ladder:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> LadderGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return _from_json(raw) if raw else None

    async def _save(self, game: LadderGame) -> None:
        # 저장할 때마다 TTL을 다시 걸어준다 — 활동이 있는 게임은 계속 살아 있고,
        # 방치된 게임은 TTL 만료로 Redis에서 자동 소멸한다.
        await get_redis().set(self._key(game.channel_id), _to_json(game), ex=int(self._ttl))

    async def _get_or_create(self, channel_id: int) -> LadderGame:
        game = await self._load(channel_id)
        if game is None:
            game = LadderGame(channel_id=channel_id)
        return game

    async def get_or_create(self, channel_id: int) -> LadderGame:
        async with self._lock(channel_id):
            game = await self._get_or_create(channel_id)
            await self._save(game)
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
        async with self._lock(channel_id):
            game = await self._get_or_create(channel_id)
            _require_waiting(game)
            self._add_entry(game.participants, game, label, added_by)
            await self._save(game)
            return game

    async def add_result(self, channel_id: int, label: str, added_by: str) -> LadderGame:
        async with self._lock(channel_id):
            game = await self._get_or_create(channel_id)
            _require_waiting(game)
            self._add_entry(game.results, game, label, added_by)
            await self._save(game)
            return game

    async def remove_participant(self, channel_id: int, entry_id: int) -> LadderGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 사다리가 없어요"
                )
            _require_waiting(game)
            game.participants = [p for p in game.participants if p.id != entry_id]
            await self._save(game)
            return game

    async def remove_result(self, channel_id: int, entry_id: int) -> LadderGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 사다리가 없어요"
                )
            _require_waiting(game)
            game.results = [r for r in game.results if r.id != entry_id]
            await self._save(game)
            return game

    async def run(self, channel_id: int, run_by: str) -> LadderGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
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
            await self._save(game)
            return game

    async def reset(self, channel_id: int) -> LadderGame:
        async with self._lock(channel_id):
            game = LadderGame(channel_id=channel_id)
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> LadderGame | None:
        async with self._lock(channel_id):
            return await self._load(channel_id)


store = LadderStore()


def get_ladder_store() -> LadderStore:
    return store
