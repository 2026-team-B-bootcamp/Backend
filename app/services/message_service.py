"""채널 메시지 저장/조회/삭제 비즈니스 로직.

routers/messages.py에서 호출된다. create_message로 저장된 메시지는
라우터 쪽에서 realtime.hub를 통해 실시간 브로드캐스트된다.

삭제는 행을 지우지 않고 deleted_at만 채우는 소프트 삭제다 — 하드 삭제로 id가
사라지면 무한 스크롤의 id 커서와 재연결 보충(after_id)이 어긋날 수 있고,
잘못 지웠을 때 되돌릴 수도 없다. 조회는 deleted_at IS NULL만 돌려준다.
"""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import KIND_USER, Message
from app.models.user import User

# (메시지, 작성자 이름, 작성자 아바타 URL) — 라우터가 응답을 조립할 때 쓰는 행 형태
MessageRow = tuple[Message, str, str | None]


async def create_message(
    db: AsyncSession,
    channel_id: int,
    user_id: int,
    content: str,
    kind: str = KIND_USER,
) -> MessageRow:
    message = Message(channel_id=channel_id, user_id=user_id, content=content, kind=kind)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    row = (
        await db.execute(select(User.display_name, User.avatar_url).where(User.id == user_id))
    ).first()
    display_name, avatar_url = row if row else ("", None)
    return message, display_name, avatar_url


async def delete_message(db: AsyncSession, channel_id: int, message_id: int, user_id: int) -> None:
    """자기가 쓴 메시지를 소프트 삭제한다.

    권한 검사는 "이 채널의 메시지인가 + 내가 쓴 것인가" 두 가지다. 남의 메시지나
    다른 채널의 메시지 id를 넣어도 404로 동일하게 응답해, 존재 여부가 새어나가지 않게 한다.
    이미 지워진 메시지를 다시 지우는 건 성공으로 친다(멱등).
    """
    message = await db.scalar(
        select(Message).where(Message.id == message_id, Message.channel_id == channel_id)
    )
    if message is None or message.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="삭제할 메시지를 찾을 수 없어요"
        )
    if message.deleted_at is not None:
        return
    message.deleted_at = datetime.now(UTC)
    await db.commit()


async def has_message_in_channel(db: AsyncSession, channel_id: int, user_id: int) -> bool:
    """이 사람이 이 채널에 남긴 메시지가 하나라도 있는지 (환영 카드 중복 방지용)."""
    found = await db.scalar(
        select(Message.id)
        .where(Message.channel_id == channel_id, Message.user_id == user_id)
        .limit(1)
    )
    return found is not None


async def list_messages(
    db: AsyncSession,
    channel_id: int,
    after_id: int | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[MessageRow]:
    # after_id가 주어지면 그 이후 메시지를(재연결 시 놓친 메시지 보충용),
    # before_id가 주어지면 그 이전 메시지를(위로 무한 스크롤용),
    # 둘 다 없으면 가장 최근 메시지 limit개를 오래된 순으로 반환한다(첫 진입용).
    stmt = (
        select(Message, User.display_name, User.avatar_url)
        .join(User, User.id == Message.user_id)
        .where(Message.channel_id == channel_id, Message.deleted_at.is_(None))
    )
    if after_id is not None:
        stmt = stmt.where(Message.id > after_id).order_by(Message.id).limit(limit)
        rows = (await db.execute(stmt)).all()
    elif before_id is not None:
        # 커서보다 오래된 메시지를 최신순으로 limit개 끊어온 뒤 오름차순으로 뒤집는다.
        # (offset 방식은 조회 사이에 새 메시지가 오면 페이지가 밀리므로 id 커서를 쓴다)
        stmt = stmt.where(Message.id < before_id).order_by(Message.id.desc()).limit(limit)
        rows = list(reversed((await db.execute(stmt)).all()))
    else:
        # Most recent `limit` messages, returned in ascending id order.
        stmt = stmt.order_by(Message.id.desc()).limit(limit)
        rows = list(reversed((await db.execute(stmt)).all()))
    return [(message, display_name, avatar_url) for message, display_name, avatar_url in rows]
