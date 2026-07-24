"""슬랙 ↔ 이음 매핑 테이블 3종.

슬랙에서 온 team/channel/user를 이음의 server/channel/user에 1:1로 붙여두는 층이다.
매핑이 있어야 슬랙에서 시작한 게임을 웹에서 이어서 할 수 있다(app/slack/mirror.py).

`platform_identities`의 UNIQUE 제약이 "슬랙 계정 하나 = 이음 계정 하나" 불변식을
DB 레벨에서 보장한다 — 애플리케이션 로직이 아니라 여기가 최종 방어선이다.
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlatformIdentity(Base):
    __tablename__ = "platform_identities"
    __table_args__ = (
        UniqueConstraint("platform", "platform_team", "platform_user_id", name="uq_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 현재는 "slack"만 들어간다. 디스코드를 붙일 때 값만 늘어나고 구조는 그대로다.
    platform: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="slack", default="slack"
    )
    platform_team: Mapped[str] = mapped_column(String(20), nullable=False)  # 슬랙 team_id (T…)
    platform_user_id: Mapped[str] = mapped_column(String(20), nullable=False)  # 슬랙 user_id (U…)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SlackWorkspace(Base):
    __tablename__ = "slack_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    team_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    # 다중 워크스페이스(P6)용. P1~P3은 .env의 봇 토큰 하나만 쓰므로 비어 있다.
    bot_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SlackChannel(Base):
    __tablename__ = "slack_channels"
    __table_args__ = (
        UniqueConstraint("team_id", "slack_channel_id", name="uq_slack_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[str] = mapped_column(String(20), nullable=False)
    slack_channel_id: Mapped[str] = mapped_column(String(20), nullable=False)  # C…
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
