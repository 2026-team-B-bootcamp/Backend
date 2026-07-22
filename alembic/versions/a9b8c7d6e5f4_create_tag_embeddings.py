"""유사 태그 매칭용 pgvector extension + tag_embeddings 테이블 생성

태그 텍스트별 Gemini 임베딩 벡터를 전역 캐시로 저장한다. 코사인 유사도가
임계값 이상인 태그 쌍("포켓몬↔피카츄")을 완전일치와 똑같이 "겹치는 관심사"로
취급하기 위한 기반이다 (services/tag_service.py).

Revision ID: a9b8c7d6e5f4
Revises: f1a2b3c4d5e6
Create Date: 2026-07-22
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = 'a9b8c7d6e5f4'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None

# 벡터 차원은 settings.tag_embedding_dim과 같아야 한다. 마이그레이션은
# 과거 시점의 스키마를 고정 기록해야 하므로 설정을 import하지 않고 값을 박는다.
EMBEDDING_DIM = 768


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.create_table(
        'tag_embeddings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tag_text', sa.String(length=30), nullable=False, unique=True),
        sa.Column('embedding', Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )
    # 코사인 거리(<=>) 검색용 HNSW 인덱스. 지금 데이터 규모엔 필수는 아니지만
    # pgvector 표준 구성이고, 태그가 늘어나도 조회 성능이 유지된다.
    op.create_index(
        'ix_tag_embeddings_embedding',
        'tag_embeddings',
        ['embedding'],
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    op.drop_index('ix_tag_embeddings_embedding', table_name='tag_embeddings')
    op.drop_table('tag_embeddings')
    # extension은 다른 곳에서 쓸 수 있으므로 내리지 않는다.
