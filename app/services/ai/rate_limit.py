"""아이스브레이커 API의 유저별 호출 횟수 제한 (남용/비용 방지).

고정 윈도우 방식: 유저별 카운터 키를 INCR로 올리고, 첫 호출 때 윈도우(1시간)
만큼 EXPIRE를 걸어둔다. 윈도우가 끝나면 키가 만료되어 카운터가 저절로 0이
된다. 카운터가 Redis에 있으므로 워커가 여러 개여도 한도가 유저당 하나로
공유된다 (인메모리 시절에는 워커 수만큼 한도가 뻥튀기됐다).
"""

from fastapi import HTTPException, status

from app.core.redis import get_redis

DEFAULT_LIMIT = 10
DEFAULT_WINDOW_SECONDS = 3600


class RateLimiter:
    def __init__(
        self,
        limit: int = DEFAULT_LIMIT,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._limit = limit
        self._window = window_seconds

    async def check(self, user_id: int) -> None:
        r = get_redis()
        key = f"ai:ratelimit:{user_id}"
        # INCR와 EXPIRE를 파이프라인으로 한 번에 보낸다. EXPIRE는 nx=True라
        # "TTL이 없을 때만" 건다 — 첫 카운트에서 윈도우를 시작하되, 만약 이전에
        # TTL이 유실된 키였다면 자가 치유된다. (예전 구현은 INCR 직후 프로세스가
        # 죽으면 TTL 없는 키가 남아 유저가 영구 429에 걸릴 수 있었다.)
        async with r.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._window, nx=True)
            count, _ = await pipe.execute()
        if count > self._limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="질문 생성 한도에 도달했어요. 잠시 후 다시 시도해주세요",
            )


limiter = RateLimiter()


def get_ai_rate_limiter() -> RateLimiter:
    return limiter
