"""아이스브레이커 질문 생성기의 추상 인터페이스.

구현체는 stub_provider(템플릿)와 gemini_provider(실제 LLM) 두 가지이며,
provider.py의 팩토리가 설정(GEMINI_API_KEY 유무)에 따라 골라준다.
질문은 "{이름}" 플레이스홀더를 포함한 템플릿 여러 개로 반환한다 — 같은 태그
조합의 질문들을 캐시해 두고 대상 이름만 바꿔 재사용하기 위해서다 (service.py 참고).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class IcebreakerProvider(ABC):
    # True면 service.py가 생성 결과를 태그 조합 키로 DB에 캐시한다.
    # (LLM 호출은 비싸므로 캐시, 템플릿 stub은 공짜이므로 매번 새로 뽑는다)
    cacheable: bool = False

    @abstractmethod
    async def generate_templates(
        self, tags: list[str], count: int, avoid: list[str] | None = None
    ) -> list[str]:
        """태그 목록으로 '{이름}' 플레이스홀더를 포함한 질문 템플릿을 count개까지 만든다.

        유저가 질문 여러 개 중 하나를 고르는 UI라서 한 번에 배치로 생성한다
        (LLM 1회 호출로 count개 — 순차 생성 대비 지연·비용 절감).
        avoid: 이미 캐시된 기존 질문들 — 겹치지 않는 새 변형을 만들 때 참고한다.
        결과가 count개보다 적을 수 있다(중복·무효 응답 제거 후).
        """
        raise NotImplementedError


@dataclass(frozen=True)
class TagInsight:
    """서버의 관심사 분포를 사람 말로 요약한 결과 (태그 설정 모달에서 보여준다)."""

    # 이 모임이 어떤 사람들인지 한두 문장으로. 예: "운동·개발 이야기가 많은 모임이에요."
    summary: str
    # 새로 들어온 사람이 골라 쓰기 좋은 태그 추천. 기존 태그 그대로가 아니라
    # 분포에서 유추한 "이 모임에서 통할 만한" 관심사여야 한다.
    suggestions: list[str]


class TagInsightProvider(ABC):
    """서버 관심사 통계를 요약·추천으로 바꿔주는 제공자.

    구현체는 stub_provider(규칙 기반)와 gemini_provider(실제 LLM) 두 가지이며,
    provider.py의 팩토리가 GEMINI_API_KEY 유무에 따라 골라준다.
    IcebreakerProvider와 같은 이유로 cacheable을 둔다 — LLM 결과만 Redis에 캐시한다.
    """

    cacheable: bool = False

    @abstractmethod
    async def summarize(
        self, tag_counts: list[tuple[str, int]], member_count: int, suggest_count: int
    ) -> TagInsight:
        """(태그, 등록 인원) 목록을 받아 한줄 요약과 추천 태그 suggest_count개를 만든다."""
        raise NotImplementedError
