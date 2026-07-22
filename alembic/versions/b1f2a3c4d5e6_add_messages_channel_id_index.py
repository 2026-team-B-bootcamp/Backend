"""add messages (channel_id, id) index for history pagination

Revision ID: b1f2a3c4d5e6
Revises: 68c4e6a9dbcb
Create Date: 2026-07-22

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1f2a3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '68c4e6a9dbcb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 메시지 히스토리 조회는 WHERE channel_id = ? AND id < ? ORDER BY id 형태다.
    # Postgres는 FK에 인덱스를 자동으로 만들지 않으므로 복합 인덱스를 직접 건다.
    op.create_index(
        'ix_messages_channel_id_id', 'messages', ['channel_id', 'id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_messages_channel_id_id', table_name='messages')
