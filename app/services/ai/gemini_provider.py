"""Gemini API를 실제로 호출하는 IcebreakerProvider 구현체.

프롬프트로 태그 목록을 주고 "{이름}" 플레이스홀더가 든 질문 템플릿 여러 개를
JSON 배열로 한 번에 받아온다 (LLM 1회 호출로 배치 생성).
호출 실패(타임아웃·API 오류·파싱 실패·플레이스홀더 누락)는 어떤 경우에도 유저
경험을 깨지 않도록 stub 템플릿으로 모자란 개수를 채운다. 생성 결과는 service.py가
태그 조합 키로 캐시하므로(cacheable=True) 같은 조합은 재호출하지 않는다.
"""

import asyncio
import json
import logging

from google import genai
from google.genai import types

from app.core.config import settings
from app.services.ai.base import IcebreakerProvider
from app.services.ai.stub_provider import TemplateIcebreakerProvider

logger = logging.getLogger(__name__)

# 질문 3개 배치 생성이라 단건(5초)보다 여유를 둔다.
TIMEOUT_SECONDS = 8.0

# {tags}/{count}만 포맷 대상이고, {{이름}}은 이스케이프라 결과물에 "{이름}"으로 남는다.
_PROMPT = """당신은 처음 만난 사람들이 서로 친해지도록 돕는 진행자입니다.
상대의 관심사: {tags}

처음 만난 상대에게 말을 걸며 던질 대화 시작 질문을 한국어로 {count}개 만들어주세요.

규칙:
- 실제 사람이 처음 만난 자리에서 물어볼 법한, 자연스럽고 편한 말투로 쓰세요.
- 질문 하나는 관심사 하나에만 집중하세요. 여러 관심사를 한 문장에 억지로 엮지 마세요.
- 그 관심사를 실제로 아는 사람이 쓸 법한 구체적인 표현을 쓰세요.
  예: 게임이면 "{{이름}}님 롤 하신다면서요? 주로 어떤 포지션 가세요?",
  사진이면 "{{이름}}님은 주로 어떤 사진 찍으세요? 찍은 사진 구경해볼 수 있어요?"
- 질문끼리는 서로 다른 관심사나 서로 다른 각도를 다루세요.
- 질문 하나는 짧은 문장 1~2개, 전체 70자 이내로 쓰세요.
- 상대를 부르는 자리는 문자 그대로 "{{이름}}님"으로 쓰세요.
- 부담스러운 개인사 질문은 금지합니다.
- 다른 설명 없이 JSON 문자열 배열로만 출력하세요. 예: ["질문1", "질문2"]"""


class GeminiIcebreakerProvider(IcebreakerProvider):
    cacheable = True

    def __init__(self, client: genai.Client | None = None) -> None:
        # client 주입은 테스트에서 가짜 클라이언트를 꽂기 위한 통로다.
        self._client = client or genai.Client(api_key=settings.gemini_api_key)
        self._fallback = TemplateIcebreakerProvider()

    async def generate_templates(
        self, tags: list[str], count: int, avoid: list[str] | None = None
    ) -> list[str]:
        real_tags = [t for t in tags if t]
        if not real_tags:
            return await self._fallback.generate_templates(tags, count)
        prompt = _PROMPT.format(tags=", ".join(real_tags), count=count)
        if avoid:
            # 변형 풀 채우기: 이미 캐시된 질문들과 소재/각도가 겹치지 않게 유도한다.
            listed = "\n".join(f"- {q}" for q in avoid)
            prompt += f"\n\n이미 있는 질문들과 소재나 각도가 겹치지 않게 만들어주세요:\n{listed}"

        results: list[str] = []
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        # 한 줄짜리 질문 생성에 추론 과정은 낭비다 — thinking을 최소화해
                        # 비용(기본값에선 응답의 8배 토큰)과 지연을 줄인다.
                        # (이 모델은 thinking_budget=0을 거부한다 — thinking_level을 쓸 것)
                        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                        max_output_tokens=400,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=TIMEOUT_SECONDS,
            )
            parsed = json.loads((response.text or "").strip())
            if not isinstance(parsed, list):
                raise ValueError(f"JSON 배열이 아님: {type(parsed).__name__}")
            seen = set(avoid or [])
            for item in parsed:
                question = str(item).strip()
                # 플레이스홀더가 없으면 캐시 재사용이 불가능한 질문이므로 버린다.
                if "{이름}" not in question or question in seen:
                    continue
                seen.add(question)
                results.append(question)
                if len(results) >= count:
                    break
            if len(results) < count:
                logger.warning(
                    "Gemini가 유효 질문을 %d/%d개만 반환 — 나머지는 stub으로 채움",
                    len(results),
                    count,
                )
        except Exception:
            logger.exception("Gemini 호출 실패 — stub 템플릿으로 폴백")

        if len(results) < count:
            fill = await self._fallback.generate_templates(
                tags, count - len(results), avoid=(avoid or []) + results
            )
            results.extend(fill)
        return results
