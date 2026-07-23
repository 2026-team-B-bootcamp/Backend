"""채널 메시지 전송/조회/삭제와 첫 입장 환영 카드를 담당하는 라우터.

요청 흐름: 클라이언트 -> 이 라우터 -> message_service(DB 저장) ->
realtime.hub(WebSocket 브로드캐스트). 메시지를 보내면 먼저 DB에 저장하고,
그 결과를 같은 채널을 보고 있는 다른 사용자들에게 실시간으로 뿌려주는
"저장 후 브로드캐스트" 패턴이 이 서비스의 실시간 채팅 핵심 로직이다.

삭제도 같은 패턴이다: 소프트 삭제(deleted_at) 후 message.deleted를 브로드캐스트해
열려 있는 모든 클라이언트에서 즉시 사라지게 한다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.message import KIND_USER, KIND_WELCOME, Message
from app.models.user import User
from app.schemas.message import MessageCreate, MessageOut
from app.services import message_service, server_service, tag_service
from app.services.ai import tag_stats
from app.services.ai import welcome as welcome_service
from app.services.ai.base import WelcomeProvider
from app.services.ai.provider import get_welcome_provider
from app.services.realtime import hub
from app.services.ws_rate_limit import allow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["messages"])


def _to_out(
    message: Message, display_name: str, avatar_url: str | None, tags: list[str]
) -> MessageOut:
    return MessageOut(
        id=message.id,
        user_id=message.user_id,
        display_name=display_name,
        avatar_url=avatar_url,
        tags=tags,
        content=message.content,
        kind=message.kind,
        created_at=message.created_at,
    )


async def _broadcast_new(channel_id: int, out: MessageOut) -> None:
    #    메시지는 이미 DB에 커밋됐으므로, 여기서 Redis가 순단해 broadcast가
    #    실패해도 500을 내면 안 된다 — 클라가 실패로 알고 재전송하면 중복이 된다.
    #    실패는 로깅만 하고 정상 응답한다(수신자는 재연결 시 after_id로 보충받는다).
    try:
        await hub.broadcast(
            channel_id, {"type": "message.new", "payload": out.model_dump(mode="json")}
        )
    except Exception:
        logger.exception("메시지 broadcast 실패(저장은 완료됨) channel_id=%s", channel_id)


@router.post("/{channel_id}/messages", response_model=MessageOut)
async def send_message(
    channel_id: int,
    payload: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    # 1) 이 채널이 속한 서버의 멤버인지 확인 (권한 검사).
    channel = await server_service.require_channel_access(db, channel_id, current_user.id)
    # 메시지는 채널 전원에게 브로드캐스트되므로 도배(fan-out 증폭)를 막는다(5초에 10건).
    if not await allow(f"flood:msg:{channel_id}:{current_user.id}", 10, 5):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="메시지를 너무 빨리 보내고 있어요. 잠시 후 다시 시도해주세요",
        )
    # 2) 메시지를 먼저 DB에 저장한다.
    message, display_name, avatar_url = await message_service.create_message(
        db, channel_id, current_user.id, payload.content, kind=KIND_USER
    )
    # 보낸 사람의 관심사 태그도 함께 실어 보내서, 채팅창에서 바로 태그를 볼 수 있게 한다.
    tags_map = await tag_service.get_server_tags_map(db, channel.server_id)
    out = _to_out(message, display_name, avatar_url, tags_map.get(message.user_id, []))
    # 3) 저장이 끝난 메시지를 같은 채널을 구독 중인 모든 WebSocket 연결에
    #    실시간으로 broadcast 한다 (DB 저장 -> 실시간 전파 순서가 핵심).
    await _broadcast_new(channel_id, out)
    return out


@router.delete("/{channel_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    channel_id: int,
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """내가 쓴 메시지를 지운다 (작성자 본인만)."""
    await server_service.require_channel_access(db, channel_id, current_user.id)
    await message_service.delete_message(db, channel_id, message_id, current_user.id)
    try:
        await hub.broadcast(
            channel_id, {"type": "message.deleted", "payload": {"id": message_id}}
        )
    except Exception:
        # 삭제는 이미 커밋됐다. 브로드캐스트 실패는 다른 클라이언트가 새로고침할 때 반영된다.
        logger.exception("메시지 삭제 broadcast 실패 channel_id=%s", channel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{channel_id}/messages/welcome", response_model=MessageOut | None)
async def welcome_message(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: WelcomeProvider = Depends(get_welcome_provider),
) -> MessageOut | None:
    """채널에 처음 들어온 사람을 위한 환영 + 자기소개 카드를 한 번만 남긴다.

    프런트가 채널에 진입할 때마다 부르지만, "이 채널에 내 메시지가 하나도 없을 때"만
    실제로 카드를 만든다. 이 조건은 DB 상태로 판정하므로 재접속·다른 기기·캐시 삭제와
    무관하게 항상 한 번이다. 이미 말한 적 있는 사람에겐 null을 돌려주고 끝난다.
    """
    channel = await server_service.require_channel_access(db, channel_id, current_user.id)
    if await message_service.has_message_in_channel(db, channel_id, current_user.id):
        return None

    tags_map = await tag_service.get_server_tags_map(db, channel.server_id)
    my_tags = [t for t in tags_map.get(current_user.id, []) if t and t.strip()]
    # 모임에서 많이 쓰이는 관심사 — 겹치는 지점을 짚어 말 걸 실마리를 만든다.
    server_tags = [tag for tag, _ in tag_stats.aggregate(tags_map)]

    content = await welcome_service.get_welcome(
        provider, current_user.display_name, my_tags, server_tags
    )
    message, display_name, avatar_url = await message_service.create_message(
        db, channel_id, current_user.id, content, kind=KIND_WELCOME
    )
    out = _to_out(message, display_name, avatar_url, my_tags)
    await _broadcast_new(channel_id, out)
    return out


@router.get("/{channel_id}/messages", response_model=list[MessageOut])
async def list_messages(
    channel_id: int,
    after_id: int | None = None,
    before_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    channel = await server_service.require_channel_access(db, channel_id, current_user.id)
    # after_id가 있으면 "그 이후 메시지"(재연결 보충), before_id가 있으면
    # "그 이전 메시지"(무한 스크롤), 없으면 "최신 메시지 목록"을 가져온다.
    rows = await message_service.list_messages(db, channel_id, after_id, before_id)
    tags_map = await tag_service.get_server_tags_map(db, channel.server_id)
    return [
        _to_out(message, display_name, avatar_url, tags_map.get(message.user_id, []))
        for message, display_name, avatar_url in rows
    ]
