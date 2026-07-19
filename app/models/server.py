"""서버(모임)와 서버 멤버십 테이블 모델.

요청 흐름에서는 server_service.py가 이 모델들을 통해 DB에 접근한다.
Server.invite_code는 초대 코드 참여 기능의 핵심 컬럼이고,
ServerMember는 "누가 어느 서버에 속해 있는지"(멤버십/권한 검사의 기준)를 저장한다.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    invite_code: Mapped[str] = mapped_column(String(8), unique=True, index=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServerMember(Base):
    __tablename__ = "server_members"
    __table_args__ = (UniqueConstraint("server_id", "user_id", name="uq_member_server_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
