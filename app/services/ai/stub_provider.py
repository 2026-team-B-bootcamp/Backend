"""IcebreakerProvider의 stub(임시) 구현체.

실제 LLM 호출 없이 미리 정해둔 템플릿에 대상 이름과 태그 하나를 끼워 넣어
질문을 만든다. IcebreakerProvider 추상화 덕분에 나중에 진짜 AI 응답으로
바꿀 때도 이 파일만 다른 구현체로 갈아끼우면 된다.
"""

import random

from app.services.ai.base import IcebreakerProvider

_TEMPLATES = [
    "{name}님은 '{tag}'에 관심이 있으시네요. 어떻게 시작하게 되셨어요?",
    "{name}님, '{tag}' 좋아하신다고 들었어요. 요즘 가장 빠져 있는 건 뭐예요?",
    "'{tag}' 이야기가 나왔는데, {name}님만의 추천이 있다면 뭐가 있을까요?",
    "{name}님과 '{tag}'에 대해 이야기 나눠보고 싶어요. 최근에 인상 깊었던 게 있나요?",
]


class TemplateIcebreakerProvider(IcebreakerProvider):
    def generate_icebreaker(self, target_name: str, tags: list[str]) -> str:
        # 빈 태그를 걸러내고, 태그가 하나도 없으면 일반 질문으로 대체한다.
        real_tags = [t for t in tags if t]
        if not real_tags:
            return f"{target_name}님에게 요즘 어떤 것에 관심이 있는지 물어보세요!"
        # 태그 하나와 템플릿 하나를 무작위로 골라 조합한다.
        tag = random.choice(real_tags)
        template = random.choice(_TEMPLATES)
        return template.format(name=target_name, tag=tag)


def get_icebreaker_provider() -> IcebreakerProvider:
    return TemplateIcebreakerProvider()
