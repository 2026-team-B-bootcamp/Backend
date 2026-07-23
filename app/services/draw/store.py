"""채널별 공유 그림판(Whiteboard) 획 목록을 Redis에 저장한다.

라우터(routers/draw.py)가 이 store를 통해 획을 쌓고, 지우고, 조회한다.
함께 보기(watch)와 마찬가지로 game_registry 락을 쓰지 않는다 —
그림판은 채팅과 공존하는 오버레이 기능이다.

저장 구조: 키 f"draw:{cid}" 에 Redis List로 담고, 각 원소는 획 하나의 JSON이다.
늦게 들어온 사람이 처음 조회(GET)할 때 지금까지의 획을 한 번에 다시 그린다.
획이 무한정 쌓이지 않도록 최근 MAX_STROKES개만 남기고, 채널마다 TTL로 자동 소멸시킨다.

List로 저장하는 이유: 예전엔 전체 획 배열을 JSON 한 덩어리로 GET→append→SET
했는데, 두 명이 동시에 그리면 서로의 append를 덮어써 획이 유실됐다(read-modify-write
경합). RPUSH는 원자적이라 동시 추가가 모두 보존된다.
"""

import json

from app.core.redis import get_redis

TTL_SECONDS = 6 * 3600
# 캔버스에 누적되는 획 상한 — 넘으면 오래된 것부터 버린다.
MAX_STROKES = 800


class DrawStore:
    def __init__(self, ttl_seconds: float = TTL_SECONDS, max_strokes: int = MAX_STROKES) -> None:
        self._ttl = ttl_seconds
        self._max = max_strokes

    def _key(self, channel_id: int) -> str:
        return f"draw:{channel_id}"

    async def get(self, channel_id: int) -> list[dict]:
        raw = await get_redis().lrange(self._key(channel_id), 0, -1)
        return [json.loads(item) for item in raw]

    async def add_stroke(self, channel_id: int, stroke: dict) -> None:
        # RPUSH(원자적 추가) + LTRIM(최근 MAX개만 유지) + EXPIRE를 파이프라인으로
        # 한 번에 처리한다. 동시에 그려도 각 RPUSH가 독립적으로 반영돼 획이 유실되지 않는다.
        key = self._key(channel_id)
        async with get_redis().pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(stroke))
            pipe.ltrim(key, -self._max, -1)
            pipe.expire(key, int(self._ttl))
            await pipe.execute()

    async def clear(self, channel_id: int) -> None:
        await get_redis().delete(self._key(channel_id))


store = DrawStore()


def get_draw_store() -> DrawStore:
    return store
