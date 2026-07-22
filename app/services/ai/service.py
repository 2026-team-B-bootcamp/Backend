"""아이스브레이커 질문 생성의 캐시 계층 — 태그 조합당 "변형 풀" 방식.

유저는 질문 여러 개(MAX_VARIANTS개) 중 하나를 골라 보내므로, 조합당 변형
풀 전체를 반환한다: 풀이 덜 찼으면 모자란 개수만큼 배치 생성해 채우고
(LLM 1회 호출), 가득 찼으면 그대로 재사용한다(LLM 호출 없음).
→ 조합당 LLM 비용은 풀 채우기 호출로 제한되면서 선택지는 다양해진다.
태그 부분 선택(예: 3개 중 1개만)도 그대로 별도 조합 키가 되어 캐시된다.
stub provider는 cacheable=False라 캐시를 건너뛰고 매번 새로 뽑는다.

캐시는 Redis 리스트("ai:questions:{태그조합}")에 TTL과 함께 저장한다 —
DB 테이블 대신 Redis를 쓰므로 조회가 빠르고, 오래 안 쓰인 조합은 TTL
만료로 자동 정리되며, 워커가 여러 개여도 캐시가 하나로 공유된다.
"""

from app.core.redis import get_redis
from app.services.ai.base import IcebreakerProvider

MAX_VARIANTS = 3
# 한 번 생성한 질문 풀은 일주일 동안 재사용한다. 그 뒤엔 자동 소멸 —
# 신선한 질문으로 자연스럽게 갱신되고, 안 쓰는 조합이 쌓이지도 않는다.
CACHE_TTL_SECONDS = 60 * 60 * 24 * 7


def _tags_key(tags: list[str]) -> str:
    # "커피,등산"과 "등산,커피"가 같은 캐시를 쓰도록 정렬해 키를 만든다.
    return ",".join(sorted({t.strip() for t in tags if t and t.strip()}))


async def get_icebreakers(
    provider: IcebreakerProvider,
    target_name: str,
    tags: list[str],
) -> list[str]:
    key = _tags_key(tags)

    if not (provider.cacheable and key):
        templates = await provider.generate_templates(tags, MAX_VARIANTS)
        return [t.replace("{이름}", target_name) for t in templates]

    r = get_redis()
    cache_key = f"ai:questions:{key}"
    variants: list[str] = await r.lrange(cache_key, 0, -1)
    # 풀이 덜 찼으면 기존 질문들과 겹치지 않는 새 변형들을 배치 생성해 채운다.
    missing = MAX_VARIANTS - len(variants)
    if missing > 0:
        fresh = await provider.generate_templates(tags, missing, avoid=variants)
        for template in fresh:
            # 플레이스홀더 누락(캐시 재사용 불가)이나 기존과 중복인 변형은 버린다.
            if "{이름}" in template and template not in variants:
                await r.rpush(cache_key, template)
                variants.append(template)
        await r.expire(cache_key, CACHE_TTL_SECONDS)

    return [v.replace("{이름}", target_name) for v in variants]
