"""채널 실시간 채팅용 WebSocket 엔드포인트.

요청 흐름: 클라이언트가 WebSocket으로 접속 -> 여기서 토큰 인증/멤버십 확인 ->
realtime.hub(연결 목록 관리 및 브로드캐스트)에 연결을 등록. 이후 메시지 전송은
routers/messages.py(REST)에서 DB 저장 후 hub를 통해 이 소켓들로 전파된다.
이 파일은 "연결 유지/입장 퇴장 알림"을, hub는 "누구에게 뿌릴지"를 담당한다.
"""

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
    # 이 채널이 속한 서버의 멤버가 아니면 접속을 거부한다 (권한 검사).
    if not await server_service.is_member(db, channel.server_id, user.id):
        await websocket.close(code=4403)
        return
    # 인증이 끝났으면 소켓 수명 동안 DB 커넥션을 붙들지 않는다.
    await db.close()

    await websocket.accept()
    # 이 채널의 구독자 목록에 등록하고, "누가 접속 중인지"를 모두에게 알린다.
    await hub.subscribe(channel_id, websocket, user.id, user.display_name)
    await hub.broadcast_presence(channel_id)
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except ValueError:
                continue
            # 클라이언트가 "타이핑 중" 신호를 보내면 같은 채널의 다른 사람들에게 전달한다.
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
        # 연결이 끊기면 구독 목록에서 제거하고 최신 접속자 목록을 다시 알린다.
        await hub.unsubscribe(channel_id, websocket)
        await hub.broadcast_presence(channel_id)
