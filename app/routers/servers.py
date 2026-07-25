"""서버(모임) 생성/참여, 채널 관리, 멤버 목록 조회를 담당하는 라우터.

요청 흐름: 클라이언트 -> 이 라우터 -> server_service/tag_service -> 모델(DB).
초대 코드로 서버에 참여하는 로직과, 멤버 목록을 보여줄 때 나와 겹치는
관심사 태그를 계산해서 함께 내려주는 로직이 이 서비스의 핵심 특징이다.
"""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.server import Server, ServerMember
from app.models.user import User
from app.schemas.channel import ChannelCreateRequest, ChannelRenameRequest, ChannelResponse
from app.schemas.server import (
    MemberResponse,
    ServerCreateRequest,
    ServerJoinRequest,
    ServerRenameRequest,
    ServerResponse,
)
from app.services import server_service, tag_service
from app.services.realtime import hub

router = APIRouter(prefix="/servers", tags=["servers"])


@router.post("", response_model=ServerResponse)
async def create_server(
    payload: ServerCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    server = await server_service.create_server(db, payload.name, current_user.id)
    return ServerResponse(
        id=server.id,
        name=server.name,
        invite_code=server.invite_code,
        owner_user_id=server.created_by,
    )


@router.post("/join", response_model=ServerResponse)
async def join_server(
    payload: ServerJoinRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    server = await server_service.join_server(db, payload.invite_code, current_user.id)
    return ServerResponse(
        id=server.id,
        name=server.name,
        invite_code=server.invite_code,
        owner_user_id=server.created_by,
    )


@router.get("", response_model=list[ServerResponse])
async def list_servers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ServerResponse]:
    servers = await server_service.list_user_servers(db, current_user.id)
    return [
        ServerResponse(
            id=s.id, name=s.name, invite_code=s.invite_code, owner_user_id=s.created_by
        )
        for s in servers
    ]


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


@router.patch("/{server_id}", response_model=ServerResponse)
async def rename_server(
    server_id: int,
    payload: ServerRenameRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    await server_service.require_membership(db, server_id, current_user.id)
    server = await server_service.rename_server(db, server_id, payload.name)
    # 서버 이름은 모든 채널의 사이드바 머리에 떠 있다. 지금 이 서버 어딘가에
    # 접속해 있는 사람들이 새로고침 없이 바뀐 이름을 보도록 채널마다 알린다.
    channels = await server_service.list_channels(db, server_id)
    for channel in channels:
        await hub.broadcast(
            channel.id,
            {
                "type": "server.renamed",
                "payload": {"server_id": server.id, "name": server.name},
            },
        )
    return ServerResponse(
        id=server.id,
        name=server.name,
        invite_code=server.invite_code,
        owner_user_id=server.created_by,
    )


@router.patch("/{server_id}/channels/{channel_id}", response_model=ChannelResponse)
async def rename_channel(
    server_id: int,
    channel_id: int,
    payload: ChannelRenameRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    await server_service.require_membership(db, server_id, current_user.id)
    channel = await server_service.rename_channel(db, server_id, channel_id, payload.name)
    await hub.broadcast(
        channel.id,
        {
            "type": "channel.renamed",
            "payload": {
                "channel_id": channel.id,
                "server_id": channel.server_id,
                "name": channel.name,
            },
        },
    )
    return ChannelResponse(id=channel.id, server_id=channel.server_id, name=channel.name)


@router.delete("/{server_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def kick_member(
    server_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """방장이 멤버 한 명을 모임에서 내보낸다. 권한 검사는 서비스가 한다."""
    channels = await server_service.list_channels(db, server_id)
    await server_service.kick_member(db, server_id, current_user.id, user_id)
    # 내보낸 사람의 화면은 다음 요청부터 403이 나지만, 그때까지는 아무 일도 없었던
    # 것처럼 보인다. 채널마다 알려서 본인은 즉시 목록으로 나가고 남은 사람들의
    # 멤버 목록도 새로고침 없이 갱신되게 한다.
    # 채널 목록을 내보내기 *전에* 읽는 이유: 사이가 나빠 나가는 것이 아니라 정리
    # 차원이라도, 멤버십이 사라진 뒤에는 그 서버를 조회할 자격을 따지는 코드에
    # 걸릴 여지가 있어 순서를 앞당겨 둔다.
    for channel in channels:
        await hub.broadcast(
            channel.id,
            {
                "type": "server.member_removed",
                "payload": {"server_id": server_id, "user_id": user_id},
            },
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{server_id}/members", response_model=list[MemberResponse])
async def list_members(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MemberResponse]:
    # 서버 멤버가 아니면 목록을 볼 수 없다 (권한 검사).
    await server_service.require_membership(db, server_id, current_user.id)
    server = await db.get(Server, server_id)
    owner_id = server.created_by if server else None

    members = await db.scalars(
        select(User)
        .join(ServerMember, ServerMember.user_id == User.id)
        .where(ServerMember.server_id == server_id)
        .order_by(User.id)
    )
    # 서버 내 모든 멤버의 태그를 한 번에 불러온 뒤, 각 멤버마다 내 태그와
    # 겹치는 항목(common_with_me)을 계산한다. 이 값으로 프런트에서
    # "나와 관심사가 겹치는 사람"을 강조해서 보여준다 (서비스 핵심 기능).
    # 완전일치뿐 아니라 임베딩 유사도가 임계값 이상인 태그(포켓몬↔피카츄)도
    # 겹치는 것으로 친다 — 서버 전체 태그에 대한 유사도 조회는 쿼리 1회다.
    tags_map = await tag_service.get_server_tags_map(db, server_id)
    my_tags = tags_map.get(current_user.id, [])
    all_tags = [t for tags in tags_map.values() for t in tags]
    similar_map = await tag_service.get_similar_map(db, my_tags, all_tags)

    response: list[MemberResponse] = []
    for member in members:
        member_tags = tags_map.get(member.id, [])
        common = (
            [] if member.id == current_user.id
            else tag_service.matched_tags(my_tags, member_tags, similar_map)
        )
        response.append(
            MemberResponse(
                user_id=member.id,
                display_name=member.display_name,
                avatar_url=member.avatar_url,
                tags=member_tags,
                common_with_me=common,
                is_owner=member.id == owner_id,
            )
        )
    return response
