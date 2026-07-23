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


class TagStatEntry(BaseModel):
    tag: str
    # 이 태그를 등록한 서버 멤버 수.
    count: int


class TagStatsResponse(BaseModel):
    """태그 설정 모달이 쓰는 "이 모임의 관심사 지형도"."""

    total_members: int
    # 태그를 하나라도 등록한 멤버 수 — total_members와 함께 "몇 명이 채웠는지"를 보여준다.
    tagged_members: int
    top_tags: list[TagStatEntry]
    # AI가 만든 한줄 요약과 추천 태그 (Redis 캐시를 거친다).
    summary: str
    suggestions: list[str]
    # 내가 이 서버에 이미 등록해둔 태그 — 모달 입력칸 초기값으로 쓴다(없으면 빈 목록).
    my_tags: list[str]
