"""IcebreakerProvider의 stub(임시) 구현체.

실제 LLM 호출 없이 미리 정해둔 템플릿에 태그 하나를 끼워 넣어 질문을 만든다.
GEMINI_API_KEY가 없는 환경(로컬 개발, CI)의 기본 동작이자,
gemini_provider가 호출에 실패했을 때의 폴백이기도 하다.
"""

import random

from app.services.ai.base import IcebreakerProvider

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
