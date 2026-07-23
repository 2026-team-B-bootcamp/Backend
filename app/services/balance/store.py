"""채널별 밸런스게임(게시글형 토론 + 제한시간) 상태를 Redis에 저장한다.

A/B 두 선택지를 게시하면 제한시간(기본 5분) 동안 채널의 모두가 투표(변경 가능)하고
짧은 의견을 남긴다. 제한시간이 지나면 finished가 되어 투표·댓글을 받지 않는다.
서버 tick 없이, 저장된 ends_at(벽시계)과 현재 시각을 비교해 종료 여부를 계산한다.
동시 요청은 채널별 Redis 분산 락으로 직렬화한다.
"""

import json
import time
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException, status

from app.core.redis import get_redis

DURATION_SECONDS = 300  # 5분
TTL_SECONDS = DURATION_SECONDS + 1800  # 종료 후에도 잠시 결과를 볼 수 있게 여유 TTL


@dataclass
class BalanceComment:
    user_id: int
    display_name: str
    side: str | None
    text: str


@dataclass
class BalanceGame:
    channel_id: int
    option_a: str
    option_b: str
    host_user_id: int
    host_name: str
    created_at: float
    duration: int = DURATION_SECONDS
    votes: dict[str, str] = field(default_factory=dict)  # {str(user_id): "a"|"b"}
    comments: list[BalanceComment] = field(default_factory=list)

    def ends_at(self) -> float:
        return self.created_at + self.duration

    def is_finished(self, now: float) -> bool:
        return now >= self.ends_at()


def _to_json(game: BalanceGame) -> str:
    return json.dumps(asdict(game))


def _from_json(raw: str) -> BalanceGame:
    data = json.loads(raw)
    data["comments"] = [BalanceComment(**c) for c in data["comments"]]
    return BalanceGame(**data)


class BalanceStore:
    def __init__(self, ttl_seconds: float = TTL_SECONDS) -> None:
        self._ttl = ttl_seconds

    def _key(self, channel_id: int) -> str:
        return f"game:balance:{channel_id}"

    def _lock(self, channel_id: int):
        return get_redis().lock(f"lock:balance:{channel_id}", timeout=10)

    async def _load(self, channel_id: int) -> BalanceGame | None:
        raw = await get_redis().get(self._key(channel_id))
        return _from_json(raw) if raw else None

    async def _save(self, game: BalanceGame) -> None:
        await get_redis().set(self._key(game.channel_id), _to_json(game), ex=int(self._ttl))

    async def start(
        self, channel_id: int, option_a: str, option_b: str, host_user_id: int, host_name: str
    ) -> BalanceGame:
        async with self._lock(channel_id):
            # 아직 진행 중인(마감 전) 판이 있으면 새 판이 그것을 덮어써 투표·의견을
            # 통째로 날리게 되므로 막는다. 마감된 판은 새로 시작할 수 있다(누구나).
            existing = await self._load(channel_id)
            if existing is not None and not existing.is_finished(time.time()):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="이미 진행 중인 밸런스게임이 있어요",
                )
            game = BalanceGame(
                channel_id=channel_id,
                option_a=option_a,
                option_b=option_b,
                host_user_id=host_user_id,
                host_name=host_name,
                created_at=time.time(),
            )
            await self._save(game)
            return game

    async def vote(self, channel_id: int, user_id: int, side: str) -> BalanceGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 밸런스게임이 없어요"
                )
            if game.is_finished(time.time()):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="이미 마감된 밸런스게임이에요"
                )
            game.votes[str(user_id)] = side
            await self._save(game)
            return game

    async def comment(
        self, channel_id: int, user_id: int, display_name: str, text: str
    ) -> BalanceGame:
        async with self._lock(channel_id):
            game = await self._load(channel_id)
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 밸런스게임이 없어요"
                )
            if game.is_finished(time.time()):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="이미 마감된 밸런스게임이에요"
                )
            # 내가 이미 투표한 쪽을 의견에 함께 표시한다
            side = game.votes.get(str(user_id))
            game.comments.append(
                BalanceComment(user_id=user_id, display_name=display_name, side=side, text=text)
            )
            await self._save(game)
            return game

    async def get(self, channel_id: int) -> BalanceGame | None:
        return await self._load(channel_id)

    async def reset(self, channel_id: int) -> None:
        await get_redis().delete(self._key(channel_id))

    async def status(self, channel_id: int) -> str:
        game = await self._load(channel_id)
        if game is None:
            return "none"
        return "finished" if game.is_finished(time.time()) else "playing"


store = BalanceStore()


def get_balance_store() -> BalanceStore:
    return store
