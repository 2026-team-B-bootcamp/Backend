import jwt
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import decode_access_token
from app.models.channel import Channel
from app.models.user import User
from app.services import server_service
from app.services.realtime import hub

router = APIRouter()


@router.websocket("/ws/channels/{channel_id}")
async def channel_ws(
    websocket: WebSocket,
    channel_id: int,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> None:
    # 브라우저 WebSocket은 커스텀 헤더를 못 붙이므로 토큰은 쿼리로 받는다.
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        await websocket.close(code=4401)
        return

    user = await db.get(User, user_id)
    channel = await db.get(Channel, channel_id)
    if user is None or channel is None:
        await websocket.close(code=4401)
        return
    if not await server_service.is_member(db, channel.server_id, user.id):
        await websocket.close(code=4403)
        return
    # 인증이 끝났으면 소켓 수명 동안 DB 커넥션을 붙들지 않는다.
    await db.close()

    await websocket.accept()
    await hub.subscribe(channel_id, websocket, user.id, user.display_name)
    await hub.broadcast_presence(channel_id)
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except ValueError:
                continue
            if data.get("type") == "typing":
                await hub.broadcast(
                    channel_id,
                    {
                        "type": "typing",
                        "payload": {"user_id": user.id, "display_name": user.display_name},
                    },
                )
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(channel_id, websocket)
        await hub.broadcast_presence(channel_id)
