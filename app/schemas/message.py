"""메시지 전송 요청과 응답(+실시간 브로드캐스트 payload)의 형태를 정의하는 스키마.

routers/messages.py에서 요청 검증에 쓰이고, MessageOut은 REST 응답뿐 아니라
WebSocket으로 그대로 브로드캐스트되는 실시간 메시지 payload로도 재사용된다.
tags는 메시지를 보낸 사람의 관심사 태그다.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class MessageOut(BaseModel):
    id: int
    user_id: int
    display_name: str
    tags: list[str] = []
    content: str
    created_at: datetime
