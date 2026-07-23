"""add messages.deleted_at (soft delete) and messages.kind (welcome cards)

Revision ID: a1b2c3d4e5f7
Revises: e5f6a7b8c9d0
Create Date: 2026-07-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 메시지 삭제는 행을 지우지 않고 시각만 남긴다(소프트 삭제).
    # 하드 삭제는 id가 사라져 무한 스크롤의 id 커서와 재연결 보충(after_id)이
    # 어긋날 수 있고, 사고 시 되돌릴 수도 없다.
    op.add_column('messages', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    # 'user'(사람이 쓴 메시지) / 'welcome'(첫 입장 시 자동 생성된 환영·자기소개 카드).
    # 서버 기본값을 둬서 기존 행이 전부 'user'로 채워진다.
    op.add_column(
        'messages',
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='user'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('messages', 'kind')
    op.drop_column('messages', 'deleted_at')
