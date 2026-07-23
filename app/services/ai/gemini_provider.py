"""Gemini API를 실제로 호출하는 Icebreaker / TagInsight / Welcome 제공자 구현체.

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
from app.services.ai.base import (
    IcebreakerProvider,
    TagInsight,
    TagInsightProvider,
    WelcomeProvider,
)
from app.services.ai.stub_provider import (
    TemplateIcebreakerProvider,
    TemplateTagInsightProvider,
    TemplateWelcomeProvider,
)

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


# 통계 요약은 짧은 JSON 한 덩어리라 질문 배치 생성보다 빨리 끝난다.
_INSIGHT_TIMEOUT_SECONDS = 6.0

_INSIGHT_PROMPT = """당신은 온라인 모임의 분위기를 한눈에 알려주는 안내자입니다.

이 모임 멤버들이 등록한 관심사 태그와 등록 인원수:
{stats}

관심사를 등록한 사람: {member_count}명

할 일 두 가지를 JSON 객체 하나로 출력하세요.

1) "summary": 이 모임이 어떤 사람들이 모인 곳인지 한국어 1~2문장(전체 80자 이내)으로 요약.
   - 태그를 그대로 나열하지 말고, 어떤 이야기가 오갈 법한 모임인지 사람 말로 쓰세요.
   - 처음 들어온 사람에게 건네는 편안한 말투로 쓰세요.
2) "suggestions": 새로 들어온 사람이 자기 관심사로 골라 쓰기 좋은 태그 {suggest_count}개(한국어).
   - 위 태그 분포와 결이 맞아 대화가 붙을 만한 것을 고르세요.
   - 이미 있는 태그를 그대로 반복해도 되고, 같은 결의 다른 관심사를 새로 제안해도 됩니다.
   - 각 태그는 공백 없는 1~10자 단어로 쓰세요.

다른 설명 없이 JSON 객체로만 출력하세요.
예: {{"summary": "...", "suggestions": ["...", "..."]}}"""


class GeminiTagInsightProvider(TagInsightProvider):
    """서버 관심사 통계를 Gemini로 요약하는 구현체.

    호출 실패(타임아웃·파싱 실패·빈 응답)는 모달을 깨뜨리지 않도록 통계 기반
    stub 요약으로 폴백한다. 결과는 tag_stats.py가 태그 분포 지문(fingerprint)을
    키로 Redis에 캐시하므로(cacheable=True), 분포가 그대로면 다시 부르지 않는다.
    """

    cacheable = True

    def __init__(self, client: genai.Client | None = None) -> None:
        self._client = client or genai.Client(api_key=settings.gemini_api_key)
        self._fallback = TemplateTagInsightProvider()

    async def summarize(
        self, tag_counts: list[tuple[str, int]], member_count: int, suggest_count: int
    ) -> TagInsight:
        if not tag_counts:
            # 통계가 없으면 LLM에 물어볼 것도 없다 — 안내 문구를 그대로 쓴다.
            return await self._fallback.summarize(tag_counts, member_count, suggest_count)

        stats = "\n".join(f"- {tag}: {count}명" for tag, count in tag_counts)
        prompt = _INSIGHT_PROMPT.format(
            stats=stats, member_count=member_count, suggest_count=suggest_count
        )
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                        max_output_tokens=400,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=_INSIGHT_TIMEOUT_SECONDS,
            )
            parsed = json.loads((response.text or "").strip())
            if not isinstance(parsed, dict):
                raise ValueError(f"JSON 객체가 아님: {type(parsed).__name__}")
            summary = str(parsed.get("summary", "")).strip()
            raw_suggestions = parsed.get("suggestions")
            suggestions: list[str] = []
            if isinstance(raw_suggestions, list):
                for item in raw_suggestions:
                    tag = str(item).strip()
                    # 태그 컬럼이 30자 제한이라 그대로 입력칸에 넣을 수 있는 것만 남긴다.
                    if tag and len(tag) <= 30 and tag not in suggestions:
                        suggestions.append(tag)
            if summary and suggestions:
                return TagInsight(summary=summary, suggestions=suggestions[:suggest_count])
            logger.warning("Gemini 관심사 요약이 불완전 — stub으로 폴백")
        except Exception:
            logger.exception("Gemini 관심사 요약 실패 — stub으로 폴백")

        return await self._fallback.summarize(tag_counts, member_count, suggest_count)


# 짧은 인사 한 문장이라 통계 요약보다도 빨리 끝난다.
_WELCOME_TIMEOUT_SECONDS = 6.0

_WELCOME_PROMPT = """당신은 온라인 모임의 입담 좋은 사회자입니다. 새로 들어온 사람을
모두에게 소개하며 분위기를 띄우는 역할이에요.

새 멤버의 관심사: {my_tags}
이 모임에서 이미 많이 나오는 관심사: {server_tags}

새 멤버가 채널에 처음 들어온 순간 채팅창에 뜰 "등장 소개"를 한국어로 한 개 만드세요.

규칙:
- 2~3문장, 전체 120자 이내.
- 새 멤버를 부르는 자리는 문자 그대로 "{{이름}}님"으로 쓰고, 반드시 한 번 넣으세요.
- 재미있게 쓰세요. 관심사를 그냥 나열하지 말고, 그 관심사를 가진 사람이 어떤 사람일지
  재치 있게 상상해 한 줄 붙이세요.
  예: 캠핑이면 "장작 패는 소리가 들리는 것 같군요", 재즈면 "새벽 감성 담당이 등장했습니다"
- 과장된 등장 소개처럼 살짝 오버해도 좋습니다. 다만 놀리거나 비꼬지는 마세요.
- 이모지는 1~2개까지만. 문장 끝이나 관심사 옆에 포인트로만 쓰세요.
- 모임의 관심사와 겹치는 게 있으면 그 지점을 짚어 "누구랑 얘기하면 되겠다"는 실마리를 주세요.
- 마지막은 다른 사람이 말을 걸고 싶어지게 마무리하세요.
- 다른 설명 없이 문구만 출력하세요."""


class GeminiWelcomeProvider(WelcomeProvider):
    """새 멤버 환영 문구를 Gemini로 만드는 구현체.

    실패(타임아웃·빈 응답·플레이스홀더 누락)는 stub 템플릿으로 폴백한다 —
    채널 첫 입장이라는 중요한 순간에 빈 화면이 뜨면 안 된다.
    결과는 welcome.py가 관심사 조합 키로 Redis에 캐시한다(cacheable=True).
    """

    cacheable = True

    def __init__(self, client: genai.Client | None = None) -> None:
        self._client = client or genai.Client(api_key=settings.gemini_api_key)
        self._fallback = TemplateWelcomeProvider()

    async def generate(self, my_tags: list[str], server_tags: list[str]) -> str:
        mine = [t for t in my_tags if t and t.strip()]
        if not mine:
            # 소개할 관심사가 없으면 LLM에 물어볼 것도 없다.
            return await self._fallback.generate(my_tags, server_tags)

        prompt = _WELCOME_PROMPT.format(
            my_tags=", ".join(mine),
            server_tags=", ".join(t for t in server_tags if t) or "(아직 없음)",
        )
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                        max_output_tokens=200,
                    ),
                ),
                timeout=_WELCOME_TIMEOUT_SECONDS,
            )
            text = (response.text or "").strip().strip('"')
            # 플레이스홀더가 없으면 이름을 끼울 자리가 없어 캐시 재사용이 불가능하다.
            if text and "{이름}" in text:
                return text
            logger.warning("Gemini 환영 문구에 {이름} 플레이스홀더 없음 — stub으로 폴백")
        except Exception:
            logger.exception("Gemini 환영 문구 생성 실패 — stub으로 폴백")

        return await self._fallback.generate(my_tags, server_tags)
