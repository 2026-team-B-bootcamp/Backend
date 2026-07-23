"""유튜브 '함께 보기(Watch Together)' API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → watch store(Redis에 파티 상태 저장)
→ 상태를 직렬화해 응답하고 realtime hub로 채널 전체에 브로드캐스트한다.
파티 상태는 전원에게 동일한 공개 정보라 게임처럼 전체 상태를 그대로 쏜다.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.watch import StartWatchRequest, SyncWatchRequest, WatchStateResponse
from app.services import server_service
from app.services.realtime import hub
from app.services.watch.store import WatchParty, WatchStore, extract_video_id, get_watch_store

router = APIRouter(prefix="/channels", tags=["watch"])

_INACTIVE = WatchStateResponse(active=False)


def _serialize(party: WatchParty) -> WatchStateResponse:
    return WatchStateResponse(
        active=True,
        video_id=party.video_id,
        playing=party.playing,
        position=party.effective_position(time.time()),
        host_user_id=party.host_user_id,
        host_name=party.host_name,
    )


async def _broadcast(channel_id: int, state: WatchStateResponse) -> None:
    await hub.broadcast(channel_id, {"type": "watch.state", "payload": state.model_dump()})


@router.post("/{channel_id}/watch/start", response_model=WatchStateResponse)
async def start_watch(
    channel_id: int,
    payload: StartWatchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WatchStore = Depends(get_watch_store),
) -> WatchStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    video_id = extract_video_id(payload.url)
    if video_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="유튜브 링크를 인식하지 못했어요",
        )
    party = await store.start(channel_id, video_id, current_user.id, current_user.display_name)
    state = _serialize(party)
    await _broadcast(channel_id, state)
    return state


@router.post("/{channel_id}/watch/sync", response_model=WatchStateResponse)
async def sync_watch(
    channel_id: int,
    payload: SyncWatchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WatchStore = Depends(get_watch_store),
) -> WatchStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    party = await store.sync(channel_id, payload.playing, payload.position)
    if party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 함께 보기가 없어요"
        )
    state = _serialize(party)
    await _broadcast(channel_id, state)
    return state


@router.post("/{channel_id}/watch/stop", response_model=WatchStateResponse)
async def stop_watch(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WatchStore = Depends(get_watch_store),
) -> WatchStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    await store.stop(channel_id)
    await _broadcast(channel_id, _INACTIVE)
    return _INACTIVE


@router.get("/{channel_id}/watch", response_model=WatchStateResponse)
async def get_watch(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WatchStore = Depends(get_watch_store),
) -> WatchStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    party = await store.get(channel_id)
    return _serialize(party) if party is not None else _INACTIVE
