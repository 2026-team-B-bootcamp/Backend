"""ai_question_cache: 조합당 1건 → 변형 풀(여러 건) 구조로 전환

Revision ID: d3e4f5a6b7c8
Revises: c7d8e9f0a1b2
Create Date: 2026-07-22

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 같은 태그 조합에 변형 질문을 여러 개 쌓을 수 있도록 UNIQUE를 일반 인덱스로 교체.
    op.drop_constraint('ai_question_cache_tags_key_key', 'ai_question_cache', type_='unique')
    op.create_index('ix_ai_question_cache_tags_key', 'ai_question_cache', ['tags_key'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ai_question_cache_tags_key', table_name='ai_question_cache')
    op.create_unique_constraint(
        'ai_question_cache_tags_key_key', 'ai_question_cache', ['tags_key']
    )
