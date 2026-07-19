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
    await server_service.require_membership(db, server_id, current_user.id)

    members = await db.scalars(
        select(User)
        .join(ServerMember, ServerMember.user_id == User.id)
        .where(ServerMember.server_id == server_id)
        .order_by(User.id)
    )
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
