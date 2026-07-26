"""서버(모임)/채널 관련 비즈니스 로직.

라우터(routers/servers.py, messages.py, ws.py)에서 호출되며, 실제 DB 접근은
여기서 처리한다. 초대 코드 생성/검증으로 서버에 참여하는 로직과, 멤버십
여부를 확인해 권한을 검사하는 로직(require_membership 등)이 핵심이다.
"""

import secrets
import string

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel
from app.models.server import Server, ServerMember
from app.models.tag import Tag

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


async def get_owner_id(db: AsyncSession, server_id: int) -> int | None:
    """서버를 만든 사람(id)을 돌려준다. 서버가 없으면 None.

    멤버 목록(list_members)에서 각 멤버가 방장인지(is_owner) 표시할 때 쓴다.
    """
    server = await db.get(Server, server_id)
    return server.created_by if server else None


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


async def rename_server(db: AsyncSession, server_id: int, name: str) -> Server:
    """서버 이름을 바꾼다. 호출부가 멤버십을 먼저 확인한 뒤 부른다.

    권한을 만든 사람으로 좁히지 않는 이유: 채널 생성(create_channel)도 멤버면
    누구나 할 수 있고, 이 서비스의 서버는 소수의 아는 사람이 쓰는 모임방이라
    '이름이 어색하면 아무나 고친다'가 실제 사용에 맞는다.
    """
    server = await db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    server.name = name
    await db.commit()
    await db.refresh(server)
    return server


async def delete_server(db: AsyncSession, server_id: int, actor_id: int) -> None:
    """모임을 통째로 지운다. 만든 사람만 할 수 있다.

    이름 변경(rename_server)은 멤버 누구나 하지만 삭제는 만든 사람으로 좁힌다 —
    내보내기(kick_member)와 같은 선이다. 되돌릴 수 없고, 실수 한 번의 피해가
    본인이 아니라 모임 전체에 간다.

    채널·메시지·멤버십·태그·슬랙 연동은 FK가 전부 ondelete=CASCADE로 걸려 있어
    servers 행 하나를 지우면 DB가 알아서 걷어낸다. 여기서 손으로 지우지 않는
    이유는 지울 대상이 늘 때마다 빠뜨릴 여지를 남기지 않기 위해서다.
    """
    server = await db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if server.created_by != actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="모임을 만든 사람만 모임을 삭제할 수 있어요",
        )
    await db.delete(server)
    await db.commit()


async def rename_channel(
    db: AsyncSession, server_id: int, channel_id: int, name: str
) -> Channel:
    """채널 이름을 바꾼다.

    channel_id만 받으면 다른 서버의 채널 id를 넣어 남의 채널 이름을 바꿀 수 있다.
    호출부가 확인한 멤버십은 server_id에 대한 것이므로, 그 채널이 정말 이 서버의
    것인지 여기서 한 번 더 대조한다.
    """
    channel = await db.get(Channel, channel_id)
    if channel is None or channel.server_id != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    channel.name = name
    await db.commit()
    await db.refresh(channel)
    return channel


async def delete_channel(
    db: AsyncSession, server_id: int, actor_id: int, channel_id: int
) -> None:
    """채널 하나를 지운다. 모임을 만든 사람만 할 수 있다.

    이름 변경(rename_channel)은 멤버 누구나 하는데 삭제만 방장으로 좁히는 이유는,
    채널이 사라지면 그 안의 대화가 통째로 같이 사라지기 때문이다. 되돌릴 수 없는
    쪽은 만든 사람에게 맡긴다 — kick_member와 같은 기준이다.

    메시지는 messages.channel_id가 ondelete=CASCADE라 DB가 함께 지운다.
    메시지 삭제(message_service)가 소프트 삭제인 것과 다른데, 그쪽은 남은 메시지의
    id 커서(무한 스크롤·재연결 보충)가 어긋나지 않게 하려는 것이고 채널이 통째로
    사라지는 여기서는 지킬 커서 자체가 없다.
    """
    server = await db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if server.created_by != actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="모임을 만든 사람만 채널을 삭제할 수 있어요",
        )

    # rename_channel과 같은 이유로 서버-채널 소유 관계를 대조한다 — 내가 방장인
    # 서버 id에 남의 채널 id를 붙여 보내는 것을 막는다.
    channel = await db.get(Channel, channel_id)
    if channel is None or channel.server_id != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    # 마지막 하나는 남긴다. 채널이 0개가 되면 프런트가 갈 곳을 잃고 서버 목록으로
    # 튕겨 나가는데, 그 모임은 그때부터 들어갈 수는 있어도 아무것도 없는 방이 된다.
    remaining = await db.scalar(
        select(func.count()).select_from(Channel).where(Channel.server_id == server_id)
    )
    if (remaining or 0) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="마지막 채널은 삭제할 수 없어요",
        )

    await db.delete(channel)
    await db.commit()


async def kick_member(
    db: AsyncSession, server_id: int, actor_id: int, target_id: int
) -> None:
    """모임을 만든 사람이 멤버 한 명을 내보낸다.

    이름 변경(rename_server)과 달리 권한을 만든 사람으로 좁힌다 — 되돌릴 수 없는
    쪽에 가깝고, 아무나 서로를 내보낼 수 있으면 그게 더 큰 사고다.

    지우는 범위는 _remove_membership이 정한다 (스스로 나가는 leave_server와 같다).

    차단이 아니라 내보내기다 — 초대 코드를 아는 사람은 다시 들어올 수 있다.
    """
    server = await db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if server.created_by != actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="모임을 만든 사람만 멤버를 내보낼 수 있어요",
        )
    if target_id == actor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신은 내보낼 수 없어요",
        )

    await _remove_membership(db, server_id, target_id)


async def leave_server(db: AsyncSession, server_id: int, user_id: int) -> None:
    """멤버가 스스로 모임에서 나간다.

    내보내기(kick_member)와 지우는 범위는 같지만 권한은 정반대다 — 남을 내보내는
    것은 방장만, 자기가 나가는 것은 누구나. 한 번 들어온 모임에서 스스로 빠져나올
    방법이 없으면 방장이 내보내 줄 때까지 기다려야 한다.

    방장만 예외로 막는다. 방장이 나가면 남은 사람 중 아무도 채널을 지우거나 멤버를
    정리할 수 없는 모임이 되고, 그 상태를 되돌릴 방법이 없다. 소유권 넘기기를 만들기
    전까지는 "모임을 삭제하라"고 안내하는 편이 정직하다.
    """
    server = await db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if server.created_by == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="모임을 만든 사람은 나갈 수 없어요. 모임을 삭제해 주세요",
        )
    await _remove_membership(db, server_id, user_id)


async def _remove_membership(db: AsyncSession, server_id: int, user_id: int) -> None:
    """멤버십과 이 서버의 관심사 태그를 지운다 (내보내기·나가기 공용).

    이미 남긴 메시지는 건드리지 않는다 — 대화 기록에 구멍이 나면 남은 사람들의
    맥락이 끊긴다. 태그를 함께 지우는 이유는 tags 테이블이 서버 단위로만 묶여
    있어(멤버십과 조인하지 않는다) 그대로 두면 떠난 사람이 관심사 통계에 계속
    잡히기 때문이다. 두 경로가 같은 함수를 쓰는 것은 이 규칙이 한쪽에서만 바뀌는
    일을 막기 위해서다.
    """
    membership = await db.scalar(
        select(ServerMember).where(
            ServerMember.server_id == server_id,
            ServerMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="이 모임의 멤버가 아니에요"
        )

    await db.delete(membership)
    tag = await db.scalar(
        select(Tag).where(Tag.server_id == server_id, Tag.user_id == user_id)
    )
    if tag is not None:
        await db.delete(tag)
    await db.commit()


async def require_channel_access(
    db: AsyncSession, channel_id: int, user_id: int
) -> Channel:
    """Return the channel if it exists and the user belongs to its server."""
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    await require_membership(db, channel.server_id, user_id)
    return channel
