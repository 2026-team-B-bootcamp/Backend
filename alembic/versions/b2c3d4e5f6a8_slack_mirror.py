"""slack mirror: platform_identities / slack_workspaces / slack_channels + guest users

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-07-24

슬랙 유저는 이메일도 비밀번호도 없다. 기존 users는 둘 다 NOT NULL이라
게스트 계정을 만들 수 없으므로 제약을 풀고 is_guest로 구분한다.

email의 UNIQUE는 그대로 둔다 — PostgreSQL은 NULL을 서로 중복으로 보지 않아
게스트가 몇 명이든 공존한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── users: 게스트 계정을 담을 수 있게 ────────────────────────────
    # server_default=false 라 기존 행은 전부 정식 계정으로 남는다(무해).
    op.add_column(
        'users',
        sa.Column('is_guest', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=True)
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=True)

    # ── ① 플랫폼 계정 ↔ 이음 계정 ────────────────────────────────────
    op.create_table(
        'platform_identities',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        # 지금 들어가는 값은 'slack' 뿐이다. 컬럼 하나를 플랫폼 중립으로 남겨두는
        # 비용이 0에 가깝고, 디스코드를 붙일 때 테이블을 새로 파지 않아도 된다.
        sa.Column('platform', sa.String(length=20), nullable=False, server_default='slack'),
        sa.Column('platform_team', sa.String(length=20), nullable=False),
        sa.Column('platform_user_id', sa.String(length=20), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # ★ "계정당 1슬롯" 불변식의 최종 방어선. 같은 슬랙 계정은 버튼을 몇 번
        #   누르든 항상 같은 이음 user_id로 수렴한다(동시 요청에도 DB가 보장).
        sa.UniqueConstraint(
            'platform', 'platform_team', 'platform_user_id', name='uq_identity'
        ),
    )

    # ── ② 슬랙 워크스페이스 ↔ 이음 서버 ──────────────────────────────
    op.create_table(
        'slack_workspaces',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('team_id', sa.String(length=20), nullable=False, unique=True),
        sa.Column('team_name', sa.String(length=200), nullable=True),
        sa.Column(
            'server_id',
            sa.Integer(),
            sa.ForeignKey('servers.id', ondelete='CASCADE'),
            nullable=False,
        ),
        # 다중 워크스페이스(P6)에서만 채운다. P1~P3은 .env의 토큰 하나를 쓴다.
        sa.Column('bot_token_enc', sa.Text(), nullable=True),
        sa.Column(
            'installed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # ── ③ 슬랙 채널 ↔ 이음 채널 ──────────────────────────────────────
    op.create_table(
        'slack_channels',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('team_id', sa.String(length=20), nullable=False),
        sa.Column('slack_channel_id', sa.String(length=20), nullable=False),
        sa.Column(
            'channel_id',
            sa.Integer(),
            sa.ForeignKey('channels.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.UniqueConstraint('team_id', 'slack_channel_id', name='uq_slack_channel'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('slack_channels')
    op.drop_table('slack_workspaces')
    op.drop_table('platform_identities')
    # 되돌리기 전에 게스트 계정이 남아 있으면 NOT NULL 복구가 실패한다.
    # 게스트는 슬랙 경유로만 생기므로 함께 정리한다(정식 계정은 건드리지 않는다).
    op.execute('DELETE FROM users WHERE is_guest = true')
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=False)
    op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=False)
    op.drop_column('users', 'is_guest')
