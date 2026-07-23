"""브로드캐스트 증폭(fan-out) 남용을 막는 유저·채널별 고정 윈도우 속도 제한.

타이핑 신호·메시지 전송·그림 획처럼 한 번의 요청이 채널 전원에게 퍼지는 경로는
한 명이 폭주하면 전체에 증폭돼 부하가 커진다(broadcast amplification). 유저×채널마다
카운터 키를 INCR로 올리고 첫 카운트에서만 윈도우만큼 EXPIRE(nx=True)를 걸어, 윈도우가
지나면 키가 만료돼 카운터가 저절로 0이 된다. 카운터가 Redis에 있으므로 워커가 여러
개여도 한도가 유저·채널당 하나로 공유된다(인메모리 시절엔 워커 수만큼 한도가 뻥튀기됐다).

services/ai/rate_limit.py와 같은 고정 윈도우 방식이지만, 여기선 예외를 던지지 않고
허용 여부(bool)만 돌려준다 — 호출부가 상황에 맞게 429를 내거나 조용히 버리게 한다.
"""

from app.core.redis import get_redis


async def allow(key: str, limit: int, window_seconds: int) -> bool:
    """고정 윈도우 안에서 이 키의 호출이 한도 이내면 True, 넘으면 False.

    INCR와 EXPIRE를 파이프라인으로 한 번에 보낸다. EXPIRE는 nx=True라 "TTL이 없을
    때만" 건다 — 첫 카운트에서 윈도우를 시작하되, 만약 TTL이 유실된 키였다면 자가
    치유된다(INCR 직후 프로세스가 죽어 TTL 없는 키가 남아 영구 차단되는 상황 방지).
    """
    r = get_redis()
    async with r.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = await pipe.execute()
    return count <= limit
