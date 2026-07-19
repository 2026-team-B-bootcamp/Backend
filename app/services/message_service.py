"""채널 메시지 저장/조회 비즈니스 로직.

routers/messages.py에서 호출된다. create_message로 저장된 메시지는
라우터 쪽에서 realtime.hub를 통해 실시간 브로드캐스트된다.
"""

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
    # after_id가 주어지면 그 이후 메시지를(새로고침/폴링용),
    # 없으면 가장 최근 메시지 limit개를 오래된 순으로 정렬해 반환한다(첫 진입용).
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
