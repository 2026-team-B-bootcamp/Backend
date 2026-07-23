"""공유 그림판(Whiteboard) API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → draw store(Redis에 획 목록 저장)
→ realtime hub로 채널 전체에 브로드캐스트한다. 획은 전원에게 동일한
공개 정보라 함께 보기(watch)처럼 그린 즉시 전체에 쏜다.
늦게 들어온 사람은 GET /draw로 지금까지의 획을 한 번에 받아 다시 그린다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.draw import DrawStateResponse, StrokeIn, StrokeOut
from app.services import server_service
from app.services.draw.store import DrawStore, get_draw_store
from app.services.realtime import hub
from app.services.ws_rate_limit import allow

router = APIRouter(prefix="/channels", tags=["draw"])


@router.post("/{channel_id}/draw/stroke", response_model=StrokeOut)
async def add_stroke(
    channel_id: int,
    payload: StrokeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: DrawStore = Depends(get_draw_store),
) -> StrokeOut:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 획은 그리는 즉시 채널 전원에게 퍼지는 고빈도 경로라 폭주를 막는다(5초에 60획).
    if not await allow(f"flood:draw:{channel_id}:{current_user.id}", 60, 5):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="그림을 너무 빨리 그리고 있어요. 잠시 후 다시 시도해주세요",
        )
    stroke = StrokeOut(**payload.model_dump(), user_id=current_user.id)
    data = stroke.model_dump()
    await store.add_stroke(channel_id, data)
    await hub.broadcast(channel_id, {"type": "draw.stroke", "payload": data})
    return stroke


@router.post("/{channel_id}/draw/clear", status_code=status.HTTP_204_NO_CONTENT)
async def clear_draw(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: DrawStore = Depends(get_draw_store),
) -> None:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    await store.clear(channel_id)
    await hub.broadcast(channel_id, {"type": "draw.clear", "payload": {}})


@router.get("/{channel_id}/draw", response_model=DrawStateResponse)
async def get_draw(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: DrawStore = Depends(get_draw_store),
) -> DrawStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    strokes = await store.get(channel_id)
    return DrawStateResponse(strokes=[StrokeOut(**s) for s in strokes])
