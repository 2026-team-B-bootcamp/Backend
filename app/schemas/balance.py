"""밸런스게임(게시글형 토론 + 제한시간) API 요청/응답 스키마."""

from typing import Literal

from pydantic import BaseModel, Field


class StartBalanceRequest(BaseModel):
    option_a: str = Field(min_length=1, max_length=40)
    option_b: str = Field(min_length=1, max_length=40)


class VoteBalanceRequest(BaseModel):
    side: Literal["a", "b"]


class CommentBalanceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class BalanceComment(BaseModel):
    user_id: int
    display_name: str
    side: Literal["a", "b"] | None
    text: str


class BalanceStateResponse(BaseModel):
    active: bool
    option_a: str | None = None
    option_b: str | None = None
    count_a: int = 0
    count_b: int = 0
    my_vote: Literal["a", "b"] | None = None
    comments: list[BalanceComment] = []
    ends_at: float | None = None  # epoch seconds — 클라이언트가 카운트다운에 쓴다
    finished: bool = False
    host_user_id: int | None = None
    host_name: str | None = None
