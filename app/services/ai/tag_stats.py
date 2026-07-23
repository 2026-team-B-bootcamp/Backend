"""서버 관심사 통계 + AI 요약의 캐시 계층.

태그를 처음 설정하는 사람에게 "이 모임엔 어떤 관심사를 가진 사람들이 있는지"를
보여주기 위한 데이터다 (routers/tags.py의 GET /servers/{id}/tags/stats).

집계(누가 어떤 태그를 몇 명 썼는지)는 DB 쿼리 한 번이라 매번 새로 계산하고,
비싼 것은 그 분포를 사람 말로 바꾸는 LLM 호출뿐이라 그 결과만 Redis에 캐시한다.

캐시 키에 "태그 분포 지문(fingerprint)"을 넣는 게 핵심이다:
- 아무도 태그를 바꾸지 않으면 지문이 같아 캐시가 그대로 재사용된다(LLM 호출 0회).
- 누군가 태그를 바꾸는 순간 지문이 달라져 자동으로 새 요약이 만들어진다.
  → 별도의 캐시 무효화 코드가 필요 없다.
"""

import hashlib
import json
import logging

from app.core.redis import get_redis
from app.services.ai.base import TagInsight, TagInsightProvider

logger = logging.getLogger(__name__)

# 통계 모달에 보여줄 상위 태그 개수와 추천 태그 개수.
TOP_TAGS = 8
SUGGEST_COUNT = 6
# 분포가 그대로인 동안 요약을 재사용하는 기간. 분포가 바뀌면 지문이 달라져 즉시 갱신되므로
# 이 TTL은 "쓰이지 않는 캐시를 언제 정리할까"에 가깝다.
CACHE_TTL_SECONDS = 60 * 60 * 24


def _fingerprint(tag_counts: list[tuple[str, int]], member_count: int) -> str:
    """태그 분포를 짧은 해시로 접는다 — 분포가 같으면 같은 키, 다르면 다른 키."""
    raw = json.dumps(
        {"counts": tag_counts, "members": member_count}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def aggregate(tags_map: dict[int, list[str]]) -> list[tuple[str, int]]:
    """{유저id: [태그...]} → [(태그, 등록 인원)] 내림차순 목록.

    한 사람이 같은 태그를 두 칸에 적어도 1명으로 센다(칸 수가 아니라 사람 수를 센다).
    동점일 땐 태그 이름순으로 정렬해 순서가 요청마다 흔들리지 않게 한다 —
    순서가 흔들리면 지문이 달라져 캐시가 무의미해진다.
    """
    counts: dict[str, int] = {}
    for tags in tags_map.values():
        for tag in {t.strip() for t in tags if t and t.strip()}:
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


async def get_insight(
    provider: TagInsightProvider,
    server_id: int,
    tag_counts: list[tuple[str, int]],
    member_count: int,
) -> TagInsight:
    """분포에 대한 AI 요약을 캐시에서 꺼내거나, 없으면 만들어 캐시에 넣는다."""
    if not provider.cacheable:
        return await provider.summarize(tag_counts, member_count, SUGGEST_COUNT)

    r = get_redis()
    cache_key = f"ai:tagstats:{server_id}:{_fingerprint(tag_counts, member_count)}"
    cached = await r.get(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            return TagInsight(summary=data["summary"], suggestions=data["suggestions"])
        except (ValueError, KeyError, TypeError):
            # 형식이 깨진 캐시는 버리고 새로 만든다.
            logger.warning("관심사 요약 캐시가 손상됨 — 재생성 (%s)", cache_key)

    insight = await provider.summarize(tag_counts, member_count, SUGGEST_COUNT)
    await r.set(
        cache_key,
        json.dumps(
            {"summary": insight.summary, "suggestions": insight.suggestions},
            ensure_ascii=False,
        ),
        ex=CACHE_TTL_SECONDS,
    )
    return insight
