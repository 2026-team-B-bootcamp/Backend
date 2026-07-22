"""Redis pub/sub 기반 실시간 허브: 채널 단위 WebSocket 팬아웃.

이전에는 소켓 목록을 프로세스 메모리에 들고 직접 뿌렸다(단일 워커 전용).
지금은 broadcast()가 Redis에 publish만 하고, 각 워커가 하나씩 띄우는
listen() 태스크가 그 메시지를 받아 "자기 프로세스에 붙은 소켓"에게만
전달한다. 워커가 몇 개로 늘어나도 모두 같은 Redis 채널을 구독하므로
어느 워커에서 발생한 이벤트든 전체 접속자에게 닿는다.

접속자 목록(presence)도 Redis 해시에 저장해 워커 간 공유한다.
routers/ws.py가 subscribe/unsubscribe를, routers/messages.py와 게임
라우터들이 broadcast를 호출하는 구조는 그대로다.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

from fastapi import WebSocket

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# Redis pub/sub 토픽 이름. listen()이 패턴 구독(chat:*)으로 전 채널을 한 번에 받는다.
_TOPIC = "chat:{channel_id}"
_PRESENCE = "presence:{channel_id}"
# 워커가 비정상 종료해 HDEL을 못 했어도 접속자 목록이 영원히 남지 않게 하는 안전장치.
_PRESENCE_TTL = 3600


@dataclass(frozen=True)
class LocalConn:
    # presence 해시에서 이 연결을 지울 때 쓸 키. 같은 유저가 탭 여러 개로
    # 접속해도 연결마다 다른 id를 가지므로 하나가 끊겨도 나머지는 남는다.
    conn_id: str
    user_id: int


class ChannelHub:
    """워커(프로세스)마다 인스턴스 하나. 로컬 소켓 목록 + Redis 연동을 담당한다."""

    def __init__(self) -> None:
        self._local: dict[int, dict[WebSocket, LocalConn]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self, channel_id: int, ws: WebSocket, user_id: int, display_name: str
    ) -> None:
        conn = LocalConn(conn_id=uuid.uuid4().hex, user_id=user_id)
        async with self._lock:
            self._local.setdefault(channel_id, {})[ws] = conn
        # 접속자 정보는 Redis 해시에 — 다른 워커에서도 이 채널의 접속자를 볼 수 있다.
        key = _PRESENCE.format(channel_id=channel_id)
        r = get_redis()
        await r.hset(
            key, conn.conn_id, json.dumps({"user_id": user_id, "display_name": display_name})
        )
        await r.expire(key, _PRESENCE_TTL)

    async def unsubscribe(self, channel_id: int, ws: WebSocket) -> None:
        async with self._lock:
            room = self._local.get(channel_id)
            conn = room.pop(ws, None) if room else None
            if room is not None and not room:
                del self._local[channel_id]
        if conn is not None:
            await get_redis().hdel(_PRESENCE.format(channel_id=channel_id), conn.conn_id)

    async def online_users(self, channel_id: int) -> list[dict]:
        entries = await get_redis().hvals(_PRESENCE.format(channel_id=channel_id))
        # 같은 유저의 다중 접속(탭 여러 개)은 한 명으로 합친다.
        seen: dict[int, str] = {}
        for raw in entries:
            data = json.loads(raw)
            seen.setdefault(data["user_id"], data["display_name"])
        return [{"user_id": uid, "display_name": name} for uid, name in seen.items()]

    async def broadcast(self, channel_id: int, event: dict) -> None:
        # 소켓에 직접 쓰지 않고 Redis에 publish만 한다. 이 메시지는 자기 자신을
        # 포함한 모든 워커의 listen()이 받아 각자의 로컬 소켓에 전달한다.
        await get_redis().publish(_TOPIC.format(channel_id=channel_id), json.dumps(event))

    async def broadcast_presence(self, channel_id: int) -> None:
        users = await self.online_users(channel_id)
        await self.broadcast(channel_id, {"type": "presence.update", "payload": {"users": users}})

    async def _deliver(self, channel_id: int, event: dict) -> None:
        # publish된 이벤트를 "이 워커에 붙은" 소켓들에게 전달한다.
        # 전송에 실패한 소켓(끊긴 연결)은 모아뒀다가 구독 해제한다.
        async with self._lock:
            sockets = list(self._local.get(channel_id, {}).keys())
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unsubscribe(channel_id, ws)

    async def listen(self) -> None:
        """Redis 구독 루프. 앱 시작 시(lifespan) 워커당 하나 백그라운드로 띄운다."""
        pubsub = get_redis().pubsub()
        await pubsub.psubscribe(_TOPIC.format(channel_id="*"))
        async for message in pubsub.listen():
            if message["type"] != "pmessage":
                continue  # 구독 확인 등 제어 메시지는 무시
            try:
                channel_id = int(message["channel"].split(":", 1)[1])
                await self._deliver(channel_id, json.loads(message["data"]))
            except Exception:
                logger.exception("pub/sub 메시지 처리 실패: %r", message)


hub = ChannelHub()
