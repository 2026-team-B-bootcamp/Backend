"""AI 아이스브레이커 API의 응답 스키마 (routers/ai.py에서 사용)."""

from pydantic import BaseModel


class IcebreakerResponse(BaseModel):
    question: str
