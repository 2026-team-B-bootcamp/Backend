"""아이스브레이커 질문 생성기의 추상 인터페이스.

지금은 stub_provider.py의 템플릿 구현체만 있지만, 나중에 실제 LLM을 호출하는
provider를 새로 만들어 이 클래스만 상속하면 라우터 코드 변경 없이 교체할 수 있다.
"""

from abc import ABC, abstractmethod


class IcebreakerProvider(ABC):
    @abstractmethod
    def generate_icebreaker(self, target_name: str, tags: list[str]) -> str:
        """Return a conversation-starter question about `target_name` given their tags."""
        raise NotImplementedError
