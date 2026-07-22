"""AI 아이스브레이커 API의 요청/응답 스키마 (routers/ai.py에서 사용)."""

from pydantic import BaseModel, Field


class IcebreakerRequest(BaseModel):
    # 질문에 쓸 관심사 선택 (대상 유저 태그의 부분집합이어야 함).
    # None이면 대상의 태그 전체를 쓴다 (구버전 클라이언트 호환).
    tags: list[str] | None = Field(default=None, max_length=3)


class IcebreakerResponse(BaseModel):
    # 유저가 하나를 골라 보낼 질문 후보들 (최대 MAX_VARIANTS개).
    questions: list[str]
