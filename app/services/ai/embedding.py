"""태그 텍스트를 벡터로 바꾸는 임베딩 프로바이더.

IcebreakerProvider(provider.py)와 같은 구조: GEMINI_API_KEY가 있으면 Gemini,
없으면 임베딩을 생략하는 Null 구현을 쓴다. 실패는 어떤 경우에도 태그 저장
자체를 막지 않는다 — 임베딩이 없는 태그는 완전일치 매칭만 되고, 나중에
같은 태그가 다시 등록되면 그때 재시도되는 셈이다.

벡터는 tag_embeddings 테이블(models/tag.py)에 저장되고, tag_service가
pgvector 코사인 유사도로 유사 태그를 찾는 데 쓴다.
"""

import asyncio
import logging
import math

from app.core.config import settings

logger = logging.getLogger(__name__)

# 짧은 태그 몇 개(최대 3개) 배치 임베딩 — 생성 모델보다 훨씬 빠르다.
TIMEOUT_SECONDS = 5.0


class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """texts 순서대로 벡터 목록을 반환한다. 실패 시 None (예외를 던지지 않는다)."""
        raise NotImplementedError


class NullEmbeddingProvider(EmbeddingProvider):
    """키가 없는 로컬/CI 환경용 — 임베딩을 만들지 않는다 (완전일치 매칭만 동작)."""

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, client=None) -> None:
        # client 주입은 테스트에서 가짜 클라이언트를 꽂기 위한 통로다.
        from google import genai

        self._client = client or genai.Client(api_key=settings.gemini_api_key)

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        from google.genai import types

        try:
            response = await asyncio.wait_for(
                self._client.aio.models.embed_content(
                    model=settings.gemini_embedding_model,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        # 유사도 비교가 목적이므로 검색용(RETRIEVAL_*)이 아닌
                        # SEMANTIC_SIMILARITY 태스크 타입을 쓴다.
                        task_type="SEMANTIC_SIMILARITY",
                        output_dimensionality=settings.tag_embedding_dim,
                    ),
                ),
                timeout=TIMEOUT_SECONDS,
            )
            embeddings = response.embeddings or []
            if len(embeddings) != len(texts):
                raise ValueError(f"요청 {len(texts)}개에 임베딩 {len(embeddings)}개가 반환됨")
            # 3072 미만 차원은 사전 정규화돼 있지 않아 직접 정규화한다.
            # 코사인 거리 자체는 스케일 무관이지만, 정규화해 두면 저장된 벡터의
            # 성질이 균일해져 이후 내적(<#>) 등 다른 연산으로 바꿔도 안전하다.
            return [_normalize(e.values) for e in embeddings]
        except Exception:
            logger.exception("Gemini 임베딩 호출 실패 — 해당 태그는 완전일치 매칭만 동작")
            return None


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        return values
    return [v / norm for v in values]


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    # Gemini 클라이언트를 요청마다 새로 만들지 않도록 싱글턴으로 재사용한다.
    global _provider
    if _provider is None:
        if settings.gemini_api_key:
            _provider = GeminiEmbeddingProvider()
        else:
            _provider = NullEmbeddingProvider()
    return _provider
