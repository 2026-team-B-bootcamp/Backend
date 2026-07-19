from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag


def tag_values(tag: Tag | None) -> list[str]:
    if tag is None:
        return []
    return [tag.tag1, tag.tag2, tag.tag3]


def common_tags(mine: list[str], theirs: list[str]) -> list[str]:
    their_set = set(theirs)
    seen: set[str] = set()
    result: list[str] = []
    for t in mine:
        if t in their_set and t not in seen:
            seen.add(t)
            result.append(t)
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
