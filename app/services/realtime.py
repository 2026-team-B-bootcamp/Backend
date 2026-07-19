"""In-memory realtime hub: channel-scoped WebSocket fan-out.

Single-process only — run uvicorn with one worker. Swap for Redis pub/sub
if the service ever needs horizontal scaling.
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
