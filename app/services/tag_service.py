"""서버별 관심사 태그(사용자당 3개) CRUD와 "공통 태그" 매칭 로직.

routers/tags.py, servers.py, messages.py에서 호출된다. 매칭이 이 서비스의
핵심 차별점 기능으로, 이름 옆에 표시할 "나와 겹치는 관심사"를 계산한다.
문자열 완전일치에 더해, Gemini 임베딩 + pgvector 코사인 유사도가 임계값
이상인 태그("포켓몬↔피카츄")도 겹치는 것으로 취급한다.
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.models.tag import Tag, TagEmbedding
from app.services.ai.embedding import get_embedding_provider

logger = logging.getLogger(__name__)


def tag_values(tag: Tag | None) -> list[str]:
    if tag is None:
        return []
    return [tag.tag1, tag.tag2, tag.tag3]


def matched_tags(
    mine: list[str], theirs: list[str], similar_map: dict[str, set[str]]
) -> list[str]:
    """상대 태그(theirs) 중 내 태그와 겹치는 것을 상대 순서대로 중복 없이 뽑는다.

    "겹친다" = 완전일치 OR 유사도 임계값 이상(similar_map, get_similar_map 결과).
    상대의 태그 값을 반환하는 이유: 유사 매칭에선 내 태그("포켓몬")와 상대
    태그("피카츄")가 다른 문자열인데, 프런트는 멤버의 태그 문자열과 대조해
    빛나게 표시하므로 상대 쪽 값이어야 한다. 완전일치는 어느 쪽이든 같은 값이라
    기존 완전일치 매칭 동작과 호환된다.
    """
    my_set = {t.strip() for t in mine if t and t.strip()}
    similar_to_mine: set[str] = set()
    for my_tag in my_set:
        similar_to_mine |= similar_map.get(my_tag, set())

    seen: set[str] = set()
    result: list[str] = []
    for t in theirs:
        norm = t.strip()
        if norm and norm not in seen and (norm in my_set or norm in similar_to_mine):
            seen.add(norm)
            result.append(t)
    return result


async def ensure_embeddings(db: AsyncSession, tags: list[str]) -> None:
    """아직 임베딩이 없는 태그 텍스트만 골라 벡터를 만들어 저장한다.

    태그 텍스트 단위 전역 캐시라 이미 등록된 태그(누가 등록했든)는 API를
    다시 부르지 않는다. 임베딩 실패는 태그 저장을 막지 않는다 — 해당 태그는
    완전일치 매칭만 되다가, 나중에 같은 텍스트가 등록될 때 재시도된다.
    """
    texts = sorted({t.strip() for t in tags if t and t.strip()})
    if not texts:
        return
    existing = set(
        await db.scalars(select(TagEmbedding.tag_text).where(TagEmbedding.tag_text.in_(texts)))
    )
    missing = [t for t in texts if t not in existing]
    if not missing:
        return

    vectors = await get_embedding_provider().embed(missing)
    if vectors is None:
        return

    # 동시 요청이 같은 태그를 꽂아도 unique 제약에 안 걸리게 충돌은 무시한다.
    stmt = pg_insert(TagEmbedding).values(
        [
            {"tag_text": text, "embedding": vector}
            for text, vector in zip(missing, vectors, strict=True)
        ]
    ).on_conflict_do_nothing(index_elements=["tag_text"])
    await db.execute(stmt)
    await db.commit()


async def get_similar_map(
    db: AsyncSession, my_tags: list[str], all_tags: list[str]
) -> dict[str, set[str]]:
    """내 태그별로, 코사인 유사도가 임계값 이상인 상대 태그 텍스트 집합을 구한다.

    tag_embeddings 셀프 조인 1회 쿼리로 서버 멤버 목록 전체를 커버한다
    (all_tags = 서버 내 모든 태그 텍스트). 임베딩이 없는 태그는 결과에서
    빠지므로 자연스럽게 완전일치만 동작한다.
    """
    mine = sorted({t.strip() for t in my_tags if t and t.strip()})
    others = sorted({t.strip() for t in all_tags if t and t.strip()})
    if not mine or not others:
        return {}

    a = aliased(TagEmbedding)
    b = aliased(TagEmbedding)
    similarity = 1 - a.embedding.cosine_distance(b.embedding)
    rows = await db.execute(
        select(a.tag_text, b.tag_text)
        .where(
            a.tag_text.in_(mine),
            b.tag_text.in_(others),
            a.tag_text != b.tag_text,
            similarity >= settings.tag_similarity_threshold,
        )
    )
    result: dict[str, set[str]] = {}
    for mine_text, theirs_text in rows:
        result.setdefault(mine_text, set()).add(theirs_text)
    return result


async def get_server_tags_map(db: AsyncSession, server_id: int) -> dict[int, list[str]]:
    rows = await db.scalars(select(Tag).where(Tag.server_id == server_id))
    return {tag.user_id: tag_values(tag) for tag in rows}


async def get_user_tags(db: AsyncSession, server_id: int, user_id: int) -> Tag | None:
    return await db.scalar(
        select(Tag).where(Tag.server_id == server_id, Tag.user_id == user_id)
    )


async def upsert_tags(
    db: AsyncSession, server_id: int, user_id: int, tag1: str, tag2: str, tag3: str
) -> Tag:
    tag = await get_user_tags(db, server_id, user_id)
    if tag is None:
        tag = Tag(server_id=server_id, user_id=user_id, tag1=tag1, tag2=tag2, tag3=tag3)
        db.add(tag)
    else:
        tag.tag1, tag.tag2, tag.tag3 = tag1, tag2, tag3
    await db.commit()
    await db.refresh(tag)
    return tag
