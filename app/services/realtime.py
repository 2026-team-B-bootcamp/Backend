"""In-memory realtime hub: channel-scoped WebSocket fan-out.

Single-process only — run uvicorn with one worker. Swap for Redis pub/sub
if the service ever needs horizontal scaling.

(한국어 설명) 채널별로 접속 중인 WebSocket 연결 목록을 메모리에 들고 있다가,
새 메시지/타이핑/입장·퇴장 이벤트를 그 채널에 접속한 모든 소켓에 전달(broadcast)하는
역할이다. routers/ws.py가 연결을 subscribe/unsubscribe 하고,
routers/messages.py가 메시지 저장 후 broadcast를 호출해 실시간 전파를 시작한다.
"""

import asyncio
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(frozen=True)
class Presence:
    user_id: int
    display_name: str


class ChannelHub:
    def __init__(self) -> None:
        self._subs: dict[int, dict[WebSocket, Presence]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self, channel_id: int, ws: WebSocket, user_id: int, display_name: str
    ) -> None:
        async with self._lock:
            self._subs.setdefault(channel_id, {})[ws] = Presence(user_id, display_name)

    async def unsubscribe(self, channel_id: int, ws: WebSocket) -> None:
        async with self._lock:
            room = self._subs.get(channel_id)
            if room is not None:
                room.pop(ws, None)
                if not room:
                    del self._subs[channel_id]

    async def online_users(self, channel_id: int) -> list[dict]:
        async with self._lock:
            room = self._subs.get(channel_id, {})
            seen: dict[int, str] = {}
            for presence in room.values():
                seen.setdefault(presence.user_id, presence.display_name)
        return [{"user_id": uid, "display_name": name} for uid, name in seen.items()]

    async def broadcast(self, channel_id: int, event: dict) -> None:
        # 해당 채널을 구독 중인 모든 소켓에 이벤트를 전송한다.
        # 전송 중 실패한 소켓(연결이 끊긴 소켓)은 dead로 모아뒀다가 구독 해제한다.
        async with self._lock:
            sockets = list(self._subs.get(channel_id, {}).keys())
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unsubscribe(channel_id, ws)

    async def broadcast_presence(self, channel_id: int) -> None:
        users = await self.online_users(channel_id)
        await self.broadcast(channel_id, {"type": "presence.update", "payload": {"users": users}})


hub = ChannelHub()
