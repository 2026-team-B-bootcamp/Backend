"""관심사 태그 등록/수정 요청과 응답의 형태를 정의하는 스키마.

routers/tags.py에서 사용한다. 태그는 항상 3개(tag1~tag3)를 한 세트로 입력받는다.
"""

from pydantic import BaseModel, Field


class TagUpsertRequest(BaseModel):
    tag1: str = Field(min_length=1, max_length=30)
    tag2: str = Field(min_length=1, max_length=30)
    tag3: str = Field(min_length=1, max_length=30)


class TagResponse(BaseModel):
    tags: list[str]
