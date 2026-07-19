"""채널(서버 안의 대화방) 테이블 모델.

서버 하나가 여러 채널을 가질 수 있고(server_id), 실시간 채팅(ws.py,
realtime.py)과 메시지(message.py)는 모두 이 채널 단위로 묶인다.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
