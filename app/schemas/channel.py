"""채널 생성 요청과 응답의 형태를 정의하는 스키마.

라우터(routers/servers.py)의 채널 생성/조회 API에서 사용한다.
"""

from pydantic import BaseModel, Field


class ChannelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ChannelResponse(BaseModel):
    id: int
    server_id: int
    name: str
