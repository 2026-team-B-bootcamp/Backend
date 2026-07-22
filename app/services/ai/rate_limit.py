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
        count = await r.incr(key)
        if count == 1:
            # 윈도우의 시작 — 이 키는 1시간 뒤 만료되며 카운터가 리셋된다.
            await r.expire(key, self._window)
        if count > self._limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="질문 생성 한도에 도달했어요. 잠시 후 다시 시도해주세요",
            )


limiter = RateLimiter()


def get_ai_rate_limiter() -> RateLimiter:
    return limiter
