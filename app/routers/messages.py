from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.message import MessageCreate, MessageOut
from app.services import message_service, server_service, tag_service
from app.services.realtime import hub

router = APIRouter(prefix="/channels", tags=["messages"])


@router.post("/{channel_id}/messages", response_model=MessageOut)
async def send_message(
    channel_id: int,
    payload: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    channel = await server_service.require_channel_access(db, channel_id, current_user.id)
    message, display_name = await message_service.create_message(
        db, channel_id, current_user.id, payload.content
    )
    tags_map = await tag_service.get_server_tags_map(db, channel.server_id)
    out = MessageOut(
        id=message.id,
        user_id=message.user_id,
        display_name=display_name,
        tags=tags_map.get(message.user_id, []),
        content=message.content,
        created_at=message.created_at,
    )
    await hub.broadcast(
        channel_id, {"type": "message.new", "payload": out.model_dump(mode="json")}
    )
    return out


@router.get("/{channel_id}/messages", response_model=list[MessageOut])
async def list_messages(
    channel_id: int,
    after_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    channel = await server_service.require_channel_access(db, channel_id, current_user.id)
    rows = await message_service.list_messages(db, channel_id, after_id)
    tags_map = await tag_service.get_server_tags_map(db, channel.server_id)
    return [
        MessageOut(
            id=message.id,
            user_id=message.user_id,
            display_name=display_name,
            tags=tags_map.get(message.user_id, []),
            content=message.content,
            created_at=message.created_at,
        )
        for message, display_name in rows
    ]
