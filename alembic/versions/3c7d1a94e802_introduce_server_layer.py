"""introduce server layer: servers/server_members, channels under servers, tags per server

기존 "채널"(초대코드+멤버십 보유)을 "서버"로 승격하고,
그 아래에 대화방 채널을 두는 구조로 무손실 변환한다.
- 기존 channels 행 → servers 행 (이름/초대코드/생성자 유지)
- 기존 channel_memberships → server_members
- 기존 channels 행은 자기 서버 소속의 채널로 남는다 (메시지 FK 그대로 유지)
- tags 는 채널 단위 → 서버 단위로 이동

Revision ID: 3c7d1a94e802
Revises: aab774f1f8d4
Create Date: 2026-07-19

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3c7d1a94e802'
down_revision: str | Sequence[str] | None = 'aab774f1f8d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'servers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('invite_code', sa.String(length=8), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        # 변환 중 채널→서버 매핑용 임시 컬럼 (마지막에 제거)
        sa.Column('src_channel_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_servers_invite_code'), 'servers', ['invite_code'], unique=True)

    op.create_table(
        'server_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'joined_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('server_id', 'user_id', name='uq_member_server_user'),
    )

    # 기존 채널 하나당 서버 하나 생성 (이름/초대코드/생성자/생성일 승계)
    op.execute(
        "INSERT INTO servers (name, invite_code, created_by, created_at, src_channel_id) "
        "SELECT name, invite_code, created_by, created_at, id FROM channels"
    )
    op.execute(
        "INSERT INTO server_members (server_id, user_id, joined_at) "
        "SELECT s.id, cm.user_id, cm.joined_at "
        "FROM channel_memberships cm JOIN servers s ON s.src_channel_id = cm.channel_id"
    )

    # 채널을 서버 하위로 이동 (기존 채널 행은 그대로 첫 채널이 되어 메시지가 유지된다)
    op.add_column('channels', sa.Column('server_id', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE channels SET server_id = s.id FROM servers s WHERE s.src_channel_id = channels.id"
    )
    op.alter_column('channels', 'server_id', nullable=False)
    op.create_foreign_key(
        'channels_server_id_fkey', 'channels', 'servers', ['server_id'], ['id'],
        ondelete='CASCADE',
    )

    # 태그: 채널 단위 → 서버 단위
    op.add_column('tags', sa.Column('server_id', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE tags SET server_id = s.id FROM servers s WHERE s.src_channel_id = tags.channel_id"
    )
    op.alter_column('tags', 'server_id', nullable=False)
    op.drop_constraint('uq_tag_channel_user', 'tags', type_='unique')
    op.drop_column('tags', 'channel_id')
    op.create_unique_constraint('uq_tag_server_user', 'tags', ['server_id', 'user_id'])
    op.create_foreign_key(
        'tags_server_id_fkey', 'tags', 'servers', ['server_id'], ['id'], ondelete='CASCADE'
    )

    # 옛 구조 정리
    op.drop_table('channel_memberships')
    op.drop_index(op.f('ix_channels_invite_code'), table_name='channels')
    op.drop_column('channels', 'invite_code')
    op.drop_column('channels', 'created_by')
    op.drop_column('servers', 'src_channel_id')


def downgrade() -> None:
    """Downgrade schema."""
    # 서버당 채널이 여러 개가 된 뒤에는 초대코드 유일성을 되살릴 수 없어 되돌리기를 지원하지 않는다.
    raise RuntimeError("server-layer migration is irreversible")
