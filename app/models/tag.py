"""사용자가 서버별로 등록하는 관심사 태그(최대 3개) 테이블 모델.

tag_service.py가 이 모델로 태그를 저장/조회하며, common_tags 함수가
이 값들을 비교해 "나와 겹치는 관심사"를 계산한다(이 서비스의 핵심 기능).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tag(Base):
    __tablename__ = "tags"
    # 한 사용자는 같은 서버에서 태그 세트를 하나만 가질 수 있다 (서버당 1세트).
    __table_args__ = (UniqueConstraint("server_id", "user_id", name="uq_tag_server_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tag1: Mapped[str] = mapped_column(String(30), nullable=False)
    tag2: Mapped[str] = mapped_column(String(30), nullable=False)
    tag3: Mapped[str] = mapped_column(String(30), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
