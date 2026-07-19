"""서버(모임) 생성/참여, 채널 관리, 멤버 목록 조회를 담당하는 라우터.

요청 흐름: 클라이언트 -> 이 라우터 -> server_service/tag_service -> 모델(DB).
초대 코드로 서버에 참여하는 로직과, 멤버 목록을 보여줄 때 나와 겹치는
관심사 태그를 계산해서 함께 내려주는 로직이 이 서비스의 핵심 특징이다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.server import ServerMember
from app.models.user import User
from app.schemas.channel import ChannelCreateRequest, ChannelResponse
from app.schemas.server import (
    MemberResponse,
    ServerCreateRequest,
    ServerJoinRequest,
    ServerResponse,
)
from app.services import server_service, tag_service

router = APIRouter(prefix="/servers", tags=["servers"])


@router.post("", response_model=ServerResponse)
async def create_server(
    payload: ServerCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    server = await server_service.create_server(db, payload.name, current_user.id)
    return ServerResponse(id=server.id, name=server.name, invite_code=server.invite_code)


@router.post("/join", response_model=ServerResponse)
async def join_server(
    payload: ServerJoinRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    server = await server_service.join_server(db, payload.invite_code, current_user.id)
    return ServerResponse(id=server.id, name=server.name, invite_code=server.invite_code)


@router.get("", response_model=list[ServerResponse])
async def list_servers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ServerResponse]:
    servers = await server_service.list_user_servers(db, current_user.id)
    return [ServerResponse(id=s.id, name=s.name, invite_code=s.invite_code) for s in servers]


@router.get("/{server_id}/channels", response_model=list[ChannelResponse])
async def list_channels(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChannelResponse]:
    await server_service.require_membership(db, server_id, current_user.id)
    channels = await server_service.list_channels(db, server_id)
    return [ChannelResponse(id=c.id, server_id=c.server_id, name=c.name) for c in channels]


@router.post("/{server_id}/channels", response_model=ChannelResponse)
async def create_channel(
    server_id: int,
    payload: ChannelCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    await server_service.require_membership(db, server_id, current_user.id)
    channel = await server_service.create_channel(db, server_id, payload.name)
    return ChannelResponse(id=channel.id, server_id=channel.server_id, name=channel.name)


@router.get("/{server_id}/members", response_model=list[MemberResponse])
async def list_members(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MemberResponse]:
    # 서버 멤버가 아니면 목록을 볼 수 없다 (권한 검사).
    await server_service.require_membership(db, server_id, current_user.id)

    members = await db.scalars(
        select(User)
        .join(ServerMember, ServerMember.user_id == User.id)
        .where(ServerMember.server_id == server_id)
        .order_by(User.id)
    )
    # 서버 내 모든 멤버의 태그를 한 번에 불러온 뒤, 각 멤버마다 내 태그와
    # 겹치는 항목(common_with_me)을 계산한다. 이 값으로 프런트에서
    # "나와 관심사가 겹치는 사람"을 강조해서 보여준다 (서비스 핵심 기능).
    tags_map = await tag_service.get_server_tags_map(db, server_id)
    my_tags = tags_map.get(current_user.id, [])

    response: list[MemberResponse] = []
    for member in members:
        member_tags = tags_map.get(member.id, [])
        common = (
            [] if member.id == current_user.id
            else tag_service.common_tags(my_tags, member_tags)
        )
        response.append(
            MemberResponse(
                user_id=member.id,
                display_name=member.display_name,
                tags=member_tags,
                common_with_me=common,
            )
        )
    return response
