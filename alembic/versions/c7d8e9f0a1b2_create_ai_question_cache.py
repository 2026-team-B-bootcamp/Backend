"""create ai_question_cache

Revision ID: c7d8e9f0a1b2
Revises: b1f2a3c4d5e6
Create Date: 2026-07-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'b1f2a3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # AI가 생성한 아이스브레이커 질문 템플릿 캐시 — 태그 조합당 1건.
    op.create_table(
        'ai_question_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tags_key', sa.String(length=120), nullable=False),
        sa.Column('template', sa.String(length=300), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tags_key'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('ai_question_cache')
