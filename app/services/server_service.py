"""서버(모임)/채널 관련 비즈니스 로직.

라우터(routers/servers.py, messages.py, ws.py)에서 호출되며, 실제 DB 접근은
여기서 처리한다. 초대 코드 생성/검증으로 서버에 참여하는 로직과, 멤버십
여부를 확인해 권한을 검사하는 로직(require_membership 등)이 핵심이다.
"""

import secrets
import string

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel
from app.models.server import Server, ServerMember

_ALPHABET = string.ascii_uppercase + string.digits

DEFAULT_CHANNEL_NAME = "일반"


def generate_invite_code(length: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


async def create_server(db: AsyncSession, name: str, creator_id: int) -> Server:
    # 겹치지 않는 초대 코드가 나올 때까지 최대 10번 시도한다.
    for _ in range(10):
        code = generate_invite_code()
        if await db.scalar(select(Server).where(Server.invite_code == code)) is None:
            break
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not allocate invite code",
        )
    server = Server(name=name, invite_code=code, created_by=creator_id)
    db.add(server)
    await db.flush()
    # 서버를 만든 사람을 첫 멤버로 등록하고, 기본 채널("일반")도 함께 만든다.
    db.add(ServerMember(server_id=server.id, user_id=creator_id))
    db.add(Channel(server_id=server.id, name=DEFAULT_CHANNEL_NAME))
    await db.commit()
    await db.refresh(server)
    return server


async def join_server(db: AsyncSession, invite_code: str, user_id: int) -> Server:
    """초대 코드로 서버에 참여한다. 이미 멤버라면 중복 등록하지 않는다."""
    server = await db.scalar(select(Server).where(Server.invite_code == invite_code))
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite code")
    existing = await db.scalar(
        select(ServerMember).where(
            ServerMember.server_id == server.id,
            ServerMember.user_id == user_id,
        )
    )
    if existing is None:
        db.add(ServerMember(server_id=server.id, user_id=user_id))
        await db.commit()
    return server


async def is_member(db: AsyncSession, server_id: int, user_id: int) -> bool:
    membership = await db.scalar(
        select(ServerMember).where(
            ServerMember.server_id == server_id,
            ServerMember.user_id == user_id,
        )
    )
    return membership is not None


async def require_membership(db: AsyncSession, server_id: int, user_id: int) -> None:
    """서버 멤버가 아니면 403 에러를 던진다. 다른 라우터들의 권한 검사 공용 함수."""
    if not await is_member(db, server_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this server"
        )


async def list_user_servers(db: AsyncSession, user_id: int) -> list[Server]:
    result = await db.scalars(
        select(Server)
        .join(ServerMember, ServerMember.server_id == Server.id)
        .where(ServerMember.user_id == user_id)
        .order_by(Server.id)
    )
    return list(result)


async def list_channels(db: AsyncSession, server_id: int) -> list[Channel]:
    result = await db.scalars(
        select(Channel).where(Channel.server_id == server_id).order_by(Channel.id)
    )
    return list(result)


async def create_channel(db: AsyncSession, server_id: int, name: str) -> Channel:
    channel = Channel(server_id=server_id, name=name)
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return channel


async def require_channel_access(
    db: AsyncSession, channel_id: int, user_id: int
) -> Channel:
    """Return the channel if it exists and the user belongs to its server."""
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    await require_membership(db, channel.server_id, user_id)
    return channel
