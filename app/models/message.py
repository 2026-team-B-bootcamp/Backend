"""채널 메시지 테이블 모델.

message_service.py가 이 모델로 메시지를 저장/조회하고, 저장된 메시지는
routers/messages.py를 거쳐 realtime.hub로 실시간 브로드캐스트된다.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# kind 값 — 사람이 쓴 일반 메시지 / 첫 입장 시 자동 생성되는 환영·자기소개 카드
KIND_USER = "user"
KIND_WELCOME = "welcome"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=KIND_USER, default=KIND_USER
    )
    # 소프트 삭제 시각. 행을 지우지 않는 이유는 id가 사라지면 무한 스크롤의 id 커서와
    # 재연결 보충(after_id)이 어긋나기 때문이다. 조회 시 이 값이 있는 행은 제외한다.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
