"""Redis 클라이언트 전역 인스턴스.

용도 3가지:
① WebSocket 브로드캐스트 pub/sub — services/realtime.py
② 게임 세션 상태 저장(TTL로 자동 소멸) — services/*/store.py, game_registry.py
③ AI 질문 캐시 + 호출 횟수 제한 — services/ai/

uvicorn 워커가 몇 개로 늘어나도 모두 같은 Redis를 바라보므로,
프로세스 메모리에 상태를 두던 이전 구조와 달리 워커 간 상태가 공유된다.
"""

import redis.asyncio as aioredis

from app.core.config import settings

# decode_responses=True: 값을 bytes가 아닌 str로 주고받는다 (json.loads에 바로 넣기 위함).
client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> aioredis.Redis:
    # 항상 이 함수를 거쳐 접근한다 — 테스트에서 client를 fakeredis로 바꿔치기하는 통로.
    return client
