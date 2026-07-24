"""User 테이블과 매핑되는 ORM 모델.
요청 흐름의 가장 아래 단계로, 라우터/서비스가 이 클래스를 통해 users 테이블을 읽고 쓴다.
비밀번호는 password_hash 컬럼에 해시된 값만 저장되고 원문은 절대 저장하지 않는다.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # unique=True: 이메일 중복 가입을 DB 레벨에서도 막는다.
    # nullable: 슬랙 경유 게스트는 이메일·비밀번호가 없다(is_guest 참고).
    # PostgreSQL은 NULL끼리 중복으로 보지 않으므로 UNIQUE와 공존한다.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 슬랙 등 외부 플랫폼에서 자동 생성된 계정. 비밀번호 로그인이 막혀 있고
    # (routers/auth.py), 나중에 "게스트→정식 계정 승격"이 붙을 자리이기도 하다.
    is_guest: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 발급된 JWT를 서버에서 일괄 무효화하기 위한 버전. 토큰에 이 값을 심어두고,
    # 로그아웃 시 +1 하면 그 이전에 발급된 모든 토큰(ver 불일치)이 즉시 거부된다.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
