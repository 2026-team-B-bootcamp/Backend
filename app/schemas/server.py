"""서버(모임) 생성/참여 요청과 응답의 형태를 정의하는 스키마.

라우터(routers/servers.py)가 요청 본문을 검증하고 응답을 만들 때 사용한다.
MemberResponse.common_with_me는 나와 겹치는 관심사 태그 목록이다.
"""

from pydantic import BaseModel, Field


class ServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ServerJoinRequest(BaseModel):
    invite_code: str = Field(min_length=1, max_length=8)


class ServerResponse(BaseModel):
    id: int
    name: str
    invite_code: str


class MemberResponse(BaseModel):
    user_id: int
    display_name: str
    # 프로필 사진. 없으면 프런트가 이름 첫 글자 아바타로 대체한다.
    avatar_url: str | None = None
    tags: list[str]
    common_with_me: list[str]
