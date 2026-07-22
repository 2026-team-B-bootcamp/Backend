"""IcebreakerProvider 팩토리 — 설정에 따라 실제 구현체를 골라준다.

GEMINI_API_KEY가 설정돼 있으면 Gemini, 없으면 stub 템플릿을 쓴다.
덕분에 키가 없는 로컬 개발/CI 환경에서도 아이스브레이커 기능이 항상 동작한다.
routers/ai.py가 FastAPI Depends로 이 팩토리를 주입받는다.
"""

from app.core.config import settings
from app.services.ai.base import IcebreakerProvider
from app.services.ai.stub_provider import TemplateIcebreakerProvider

_provider: IcebreakerProvider | None = None


def get_icebreaker_provider() -> IcebreakerProvider:
    # Gemini 클라이언트를 요청마다 새로 만들지 않도록 싱글턴으로 재사용한다.
    global _provider
    if _provider is None:
        if settings.gemini_api_key:
            # 키가 없을 때 google-genai import 비용을 피하려고 지연 import 한다.
            from app.services.ai.gemini_provider import GeminiIcebreakerProvider

            _provider = GeminiIcebreakerProvider()
        else:
            _provider = TemplateIcebreakerProvider()
    return _provider
