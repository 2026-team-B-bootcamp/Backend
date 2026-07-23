"""IcebreakerProvider / TagInsightProvider의 stub(임시) 구현체.

실제 LLM 호출 없이 미리 정해둔 템플릿과 규칙으로 결과를 만든다.
GEMINI_API_KEY가 없는 환경(로컬 개발, CI)의 기본 동작이자,
gemini_provider가 호출에 실패했을 때의 폴백이기도 하다.
"""

import random

from app.services.ai.base import IcebreakerProvider, TagInsight, TagInsightProvider

# "{이름}"은 service.py가 대상 이름으로, "{tag}"는 여기서 태그로 치환한다.
_TEMPLATES = [
    "{이름}님은 '{tag}'에 관심이 있으시네요. 어떻게 시작하게 되셨어요?",
    "{이름}님, '{tag}' 좋아하신다고 들었어요. 요즘 가장 빠져 있는 건 뭐예요?",
    "'{tag}' 이야기가 나왔는데, {이름}님만의 추천이 있다면 뭐가 있을까요?",
    "{이름}님과 '{tag}'에 대해 이야기 나눠보고 싶어요. 최근에 인상 깊었던 게 있나요?",
]

_NO_TAG_TEMPLATE = "{이름}님에게 요즘 어떤 것에 관심이 있는지 물어보세요!"


class TemplateIcebreakerProvider(IcebreakerProvider):
    cacheable = False

    async def generate_templates(
        self, tags: list[str], count: int, avoid: list[str] | None = None
    ) -> list[str]:
        # 빈 태그를 걸러내고, 태그가 하나도 없으면 일반 질문 하나로 대체한다.
        real_tags = [t for t in tags if t]
        if not real_tags:
            return [_NO_TAG_TEMPLATE]
        # (템플릿 × 태그) 조합을 섞어 서로 다른 질문 count개를 뽑는다.
        combos = [(tpl, tag) for tpl in _TEMPLATES for tag in real_tags]
        random.shuffle(combos)
        seen = set(avoid or [])
        results: list[str] = []
        for tpl, tag in combos:
            question = tpl.replace("{tag}", tag)
            if question in seen:
                continue
            seen.add(question)
            results.append(question)
            if len(results) >= count:
                break
        return results


# 키가 없을 때 보여줄 무난한 관심사 후보 — 추천이 아예 비어 모달이 허전해지는 것을 막는다.
_FALLBACK_SUGGESTIONS = [
    "커피",
    "산책",
    "영화",
    "음악",
    "여행",
    "운동",
    "게임",
    "사진",
]


class TemplateTagInsightProvider(TagInsightProvider):
    """LLM 없이 통계만으로 요약·추천을 만드는 폴백 구현체.

    요약은 상위 태그를 그대로 엮어 문장을 만들고, 추천은 "이미 많이 쓰이는 태그"를
    앞세운 뒤 모자란 만큼 무난한 후보로 채운다. 정보량은 적지만 항상 동작한다.
    """

    cacheable = False

    async def summarize(
        self, tag_counts: list[tuple[str, int]], member_count: int, suggest_count: int
    ) -> TagInsight:
        top = [tag for tag, _ in tag_counts[:3]]
        if not top:
            summary = "아직 관심사를 등록한 사람이 없어요. 첫 태그의 주인공이 되어보세요!"
        else:
            joined = " · ".join(top)
            summary = f"{member_count}명이 관심사를 등록했고, {joined} 이야기가 가장 많아요."

        # 이미 여러 명이 쓰는 태그를 먼저 권한다 — 겹칠 확률이 높아 대화가 붙기 쉽다.
        picked = [tag for tag, _ in tag_counts[:suggest_count]]
        for candidate in _FALLBACK_SUGGESTIONS:
            if len(picked) >= suggest_count:
                break
            if candidate not in picked:
                picked.append(candidate)
        return TagInsight(summary=summary, suggestions=picked[:suggest_count])
