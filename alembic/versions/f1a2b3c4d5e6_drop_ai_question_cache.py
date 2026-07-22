"""AI 질문 캐시를 DB 테이블에서 Redis로 이전 — ai_question_cache 삭제

캐시는 성격상 "없어져도 다시 만들면 되는" 데이터라 굳이 영속 DB에 둘 이유가
없었다. Redis 리스트 + TTL(services/ai/service.py)로 옮기면서 오래 안 쓰인
조합이 자동 정리되고, 워커 여러 개가 캐시 하나를 공유한다.

Revision ID: f1a2b3c4d5e6
Revises: d3e4f5a6b7c8
Create Date: 2026-07-22
"""

import sqlalchemy as sa
from alembic import op

revision = 'f1a2b3c4d5e6'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('ix_ai_question_cache_tags_key', table_name='ai_question_cache')
    op.drop_table('ai_question_cache')


def downgrade() -> None:
    op.create_table(
        'ai_question_cache',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tags_key', sa.String(length=120), nullable=False),
        sa.Column('template', sa.String(length=300), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )
    op.create_index('ix_ai_question_cache_tags_key', 'ai_question_cache', ['tags_key'])
