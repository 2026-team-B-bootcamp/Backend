from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.models.user import User


async def create_message(
    db: AsyncSession, channel_id: int, user_id: int, content: str
) -> tuple[Message, str]:
    message = Message(channel_id=channel_id, user_id=user_id, content=content)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    display_name = await db.scalar(select(User.display_name).where(User.id == user_id))
    return message, display_name


async def list_messages(
    db: AsyncSession, channel_id: int, after_id: int | None = None, limit: int = 50
) -> list[tuple[Message, str]]:
    stmt = (
        select(Message, User.display_name)
        .join(User, User.id == Message.user_id)
        .where(Message.channel_id == channel_id)
    )
    if after_id is not None:
        stmt = stmt.where(Message.id > after_id).order_by(Message.id).limit(limit)
        rows = (await db.execute(stmt)).all()
    else:
        # Most recent `limit` messages, returned in ascending id order.
        stmt = stmt.order_by(Message.id.desc()).limit(limit)
        rows = list(reversed((await db.execute(stmt)).all()))
    return [(message, display_name) for message, display_name in rows]
