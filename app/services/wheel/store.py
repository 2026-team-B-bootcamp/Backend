"""돌림판(룰렛) 게임 세션의 항목/추첨 결과를 Redis에 저장하고 전이시키는 저장소.

routers/wheel.py의 각 엔드포인트가 호출하는 진입점이다. 별도의 logic 모듈 없이
추첨 로직(무작위 선택)은 spin()에서 바로 처리한다.

저장 방식: 게임 하나를 JSON으로 직렬화해 "game:wheel:{채널id}" 키에 TTL과 함께
저장한다. 방치된 게임은 Redis가 TTL 만료로 알아서 지우므로 별도의 청소 로직이
필요 없고(이전의 _sweep/last_touched는 그래서 사라졌다), 워커가 여러 개여도 모두
같은 상태를 본다. 동시 요청은 채널별 Redis 분산 락으로 직렬화한다 (이전의
asyncio.Lock은 한 프로세스 안에서만 유효했다).
"""

import json
import random
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis

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


def _to_json(game: WheelGame) -> str:
    return json.dumps(asdict(game))


def _from_json(raw: str) -> WheelGame:
    data = json.loads(raw)
    data["options"] = [WheelOptionEntry(**o) for o in data["options"]]
    return WheelGame(**data)


class WheelStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        rng: random.Random | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._rng = rng or random.Random()

    def _key(self, channel_id: int) -> str:
        return f"game:wheel:{channel_id}"

    def _lock(self, channel_id: int):
        # 같은 채널에 대한 동시 요청을 워커에 상관없이 한 줄로 세우는 분산 락.
        return get_redis().lock(f"lock:wheel:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> WheelGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return _from_json(raw) if raw else None

    async def _save(self, game: WheelGame) -> None:
        # 저장할 때마다 TTL을 다시 걸어준다 — 활동이 있는 게임은 계속 살아 있고,
        # 방치된 게임은 TTL 만료로 Redis에서 자동 소멸한다.
        await get_redis().set(self._key(game.channel_id), _to_json(game), ex=int(self._ttl))

    async def get_or_create(self, channel_id: int) -> WheelGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                game = WheelGame(channel_id=channel_id)
                await self._save(game)
            return game

    async def add_option(self, channel_id: int, label: str, added_by: str) -> WheelGame:
        label = label.strip()
        if not label or len(label) > MAX_LABEL_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"항목은 1~{MAX_LABEL_LENGTH}자로 입력하세요",
            )
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                game = WheelGame(channel_id=channel_id)
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
            await self._save(game)
            return game

    async def remove_option(self, channel_id: int, option_id: int) -> WheelGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
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
            await self._save(game)
            return game

    async def spin(self, channel_id: int, spun_by: str) -> WheelGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
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
            await self._save(game)
            return game

    async def reset(self, channel_id: int) -> WheelGame:
        async with self._lock(channel_id):
            game = WheelGame(channel_id=channel_id)
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> WheelGame | None:
        async with self._lock(channel_id):
            return await self._load(channel_id)


store = WheelStore()


def get_wheel_store() -> WheelStore:
    return store
