"""사용자가 서버별로 등록하는 관심사 태그(최대 3개) 테이블 모델.

tag_service.py가 이 모델로 태그를 저장/조회하며, matched_tags 함수가
이 값들을 비교해 "나와 겹치는 관심사"를 계산한다(이 서비스의 핵심 기능).
완전일치에 더해 TagEmbedding의 벡터 유사도로 유사 태그도 겹치는 것으로 본다.
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
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


class TagEmbedding(Base):
    """태그 텍스트별 Gemini 임베딩 벡터 (유저/서버와 무관한 전역 캐시).

    같은 태그 텍스트는 누가 등록하든 벡터가 같으므로 텍스트 단위로 한 번만
    임베딩한다. tag_service.get_similar_map이 pgvector 코사인 연산(<=>)으로
    이 벡터들을 비교해 "포켓몬↔피카츄" 같은 유사 태그를 찾는다.
    """

    __tablename__ = "tag_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # strip 정규화한 태그 텍스트. tags 테이블의 값과 같은 30자 제한.
    tag_text: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.tag_embedding_dim), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
