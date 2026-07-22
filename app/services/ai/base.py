"""아이스브레이커 질문 생성기의 추상 인터페이스.

구현체는 stub_provider(템플릿)와 gemini_provider(실제 LLM) 두 가지이며,
provider.py의 팩토리가 설정(GEMINI_API_KEY 유무)에 따라 골라준다.
질문은 "{이름}" 플레이스홀더를 포함한 템플릿 여러 개로 반환한다 — 같은 태그
조합의 질문들을 캐시해 두고 대상 이름만 바꿔 재사용하기 위해서다 (service.py 참고).
"""

from abc import ABC, abstractmethod


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
