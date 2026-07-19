"""돌림판(룰렛) 게임 API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → WheelStore(항목/추첨 결과 관리) →
WheelStateResponse로 변환해 응답 + 웹소켓으로 전원에게 브로드캐스트.
추첨(무작위 선택) 로직 자체는 WheelStore.spin()에 있다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.wheel import AddWheelOptionRequest, WheelOption, WheelStateResponse
from app.services import server_service
from app.services.game_registry import GameRegistry, get_game_registry
from app.services.realtime import hub
from app.services.wheel.store import WheelGame, WheelStore, get_wheel_store

router = APIRouter(prefix="/channels", tags=["wheel"])


def _serialize(game: WheelGame) -> WheelStateResponse:
    return WheelStateResponse(
        options=[
            WheelOption(id=o.id, label=o.label, added_by=o.added_by) for o in game.options
        ],
        result_option_id=game.result_option_id,
        spun_by=game.spun_by,
    )


async def _broadcast_state(channel_id: int, state: WheelStateResponse) -> None:
    # 돌림판 상태는 전원 공개 정보라 전체 상태를 그대로 쏜다.
    await hub.broadcast(channel_id, {"type": "wheel.state", "payload": state.model_dump()})


@router.post("/{channel_id}/wheel/join", response_model=WheelStateResponse)
async def join_wheel(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WheelStore = Depends(get_wheel_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> WheelStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    await registry.acquire(channel_id, "wheel")
    game = await store.get_or_create(channel_id)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/wheel/options", response_model=WheelStateResponse)
async def add_wheel_option(
    channel_id: int,
    payload: AddWheelOptionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WheelStore = Depends(get_wheel_store),
) -> WheelStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.add_option(channel_id, payload.label, current_user.display_name)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.delete("/{channel_id}/wheel/options/{option_id}", response_model=WheelStateResponse)
async def remove_wheel_option(
    channel_id: int,
    option_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WheelStore = Depends(get_wheel_store),
) -> WheelStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.remove_option(channel_id, option_id)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/wheel/spin", response_model=WheelStateResponse)
async def spin_wheel(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WheelStore = Depends(get_wheel_store),
) -> WheelStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 등록된 항목 중 하나를 무작위로 뽑아 이번 라운드 결과로 고정하는 게 여기서 일어난다.
    game = await store.spin(channel_id, current_user.display_name)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/wheel/reset", response_model=WheelStateResponse)
async def reset_wheel(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WheelStore = Depends(get_wheel_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> WheelStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.reset(channel_id)
    # 리셋은 이번 라운드를 접는다는 뜻 — 채널을 다른 게임에게 내준다.
    await registry.release(channel_id, "wheel")
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.get("/{channel_id}/wheel", response_model=WheelStateResponse)
async def get_wheel(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WheelStore = Depends(get_wheel_store),
) -> WheelStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.get(channel_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 돌림판이 없어요"
        )
    return _serialize(game)
