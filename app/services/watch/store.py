"""채널별 '함께 보기(Watch Together)' 상태를 Redis에 저장하고 동기화한다.

라우터(routers/watch.py)가 이 store를 통해 파티를 시작/동기화/종료한다.
게임과 달리 game_registry 락을 쓰지 않는다 — 함께 보기는 채팅과 공존하는 오버레이 기능이다.

동기화 원리: 재생 위치를 저장할 때 그 시점의 벽시계(time.time)를 anchor로 함께 저장한다.
읽을 때 재생 중이면 (지금 - anchor)만큼 위치를 앞당겨 "지금 있어야 할 위치"를 돌려준다.
벽시계는 워커 간(같은 머신)에서 일관되므로 여러 워커에서도 같은 위치를 계산한다.
"""

import json
import re
import time
from dataclasses import asdict, dataclass

from fastapi import HTTPException, status

from app.core.redis import get_redis

TTL_SECONDS = 6 * 3600


def extract_video_id(url: str) -> str | None:
    """유튜브 URL 또는 11자리 영상 ID 문자열에서 영상 ID를 뽑아낸다."""
    s = url.strip()
    # 이미 영상 ID만 온 경우
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    # youtu.be/<id>, youtube.com/watch?v=<id>, /shorts/<id>, /embed/<id>
    patterns = [
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"/shorts/([A-Za-z0-9_-]{11})",
        r"/embed/([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            return m.group(1)
    return None


@dataclass
class WatchParty:
    channel_id: int
    video_id: str
    playing: bool
    position: float
    anchor: float  # position을 저장한 시점의 time.time()
    host_user_id: int
    host_name: str

    def effective_position(self, now: float) -> float:
        # 재생 중이면 저장 이후 흐른 시간만큼 위치를 앞당긴다.
        if self.playing:
            return max(0.0, self.position + (now - self.anchor))
        return self.position


class WatchStore:
    def __init__(self, ttl_seconds: float = TTL_SECONDS) -> None:
        self._ttl = ttl_seconds

    def _key(self, channel_id: int) -> str:
        return f"watch:{channel_id}"

    async def _save(self, party: WatchParty) -> None:
        await get_redis().set(
            self._key(party.channel_id), json.dumps(asdict(party)), ex=int(self._ttl)
        )

    async def get(self, channel_id: int) -> WatchParty | None:
        raw = await get_redis().get(self._key(channel_id))
        return WatchParty(**json.loads(raw)) if raw else None

    async def start(
        self, channel_id: int, video_id: str, host_user_id: int, host_name: str
    ) -> WatchParty:
        # 이미 열린 파티가 있으면 새로 시작하는 순간 영상·재생 위치가 통째로 덮어써지므로
        # 막는다. 파티는 종료(stop) 전까지 계속 활성 상태다. 종료 후엔 누구나 다시 시작 가능.
        if await self.get(channel_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="이미 진행 중인 함께 보기가 있어요",
            )
        party = WatchParty(
            channel_id=channel_id,
            video_id=video_id,
            playing=True,
            position=0.0,
            anchor=time.time(),
            host_user_id=host_user_id,
            host_name=host_name,
        )
        await self._save(party)
        return party

    async def sync(self, channel_id: int, playing: bool, position: float) -> WatchParty | None:
        party = await self.get(channel_id)
        if party is None:
            return None
        party.playing = playing
        party.position = max(0.0, position)
        party.anchor = time.time()
        await self._save(party)
        return party

    async def stop(self, channel_id: int) -> None:
        await get_redis().delete(self._key(channel_id))


store = WatchStore()


def get_watch_store() -> WatchStore:
    return store
