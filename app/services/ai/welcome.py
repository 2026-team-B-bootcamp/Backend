"""새 멤버 환영 문구의 캐시 계층.

routers/messages.py의 환영 엔드포인트가 쓴다. 아이스브레이커(service.py)와 같은 발상이다:
문구를 "{이름}" 플레이스홀더가 든 템플릿으로 만들어 관심사 조합을 키로 캐시하고,
쓸 때 이름만 끼워 넣는다. 관심사가 같은 사람이 여러 명 들어와도 LLM은 한 번만 부른다.

stub provider는 cacheable=False라 캐시를 건너뛴다(공짜라 캐시할 이유가 없다).
"""

from app.core.redis import get_redis
from app.services.ai.base import WelcomeProvider

# 관심사 조합이 같으면 하루 동안 같은 문구를 재사용한다.
CACHE_TTL_SECONDS = 60 * 60 * 24
# 캐시 키에 넣을 모임 관심사 개수 — 너무 많이 넣으면 조합이 매번 달라져 캐시가 안 먹는다.
SERVER_TAGS_FOR_KEY = 5


def _combo_key(my_tags: list[str], server_tags: list[str]) -> str:
    mine = ",".join(sorted({t.strip() for t in my_tags if t and t.strip()}))
    theirs = ",".join(sorted({t.strip() for t in server_tags if t and t.strip()}))
    return f"{mine}|{theirs}"


async def get_welcome(
    provider: WelcomeProvider,
    display_name: str,
    my_tags: list[str],
    server_tags: list[str],
) -> str:
    top_server_tags = server_tags[:SERVER_TAGS_FOR_KEY]

    if not provider.cacheable:
        template = await provider.generate(my_tags, top_server_tags)
        return template.replace("{이름}", display_name)

    r = get_redis()
    cache_key = f"ai:welcome:{_combo_key(my_tags, top_server_tags)}"
    template = await r.get(cache_key)
    if not template:
        template = await provider.generate(my_tags, top_server_tags)
        # 플레이스홀더가 없는 문구는 다른 사람에게 재사용할 수 없으니 캐시하지 않는다.
        if "{이름}" in template:
            await r.set(cache_key, template, ex=CACHE_TTL_SECONDS)
    return template.replace("{이름}", display_name)
