from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.ladder import AddLadderEntryRequest, LadderEntry, LadderStateResponse
from app.services import server_service
from app.services.game_registry import GameRegistry, get_game_registry
from app.services.ladder.store import LadderGame, LadderStore, get_ladder_store
from app.services.realtime import hub

router = APIRouter(prefix="/channels", tags=["ladder"])


def _serialize(game: LadderGame) -> LadderStateResponse:
    return LadderStateResponse(
        status=game.status,
        participants=[
            LadderEntry(id=e.id, label=e.label, added_by=e.added_by) for e in game.participants
        ],
        results=[LadderEntry(id=e.id, label=e.label, added_by=e.added_by) for e in game.results],
        rungs=game.rungs,
        assignment=game.assignment,
        run_by=game.run_by,
    )


async def _broadcast_state(channel_id: int, state: LadderStateResponse) -> None:
    # 사다리 상태(참가자/결과/최종 배치)는 전원 공개 정보라 전체 상태를 그대로 쏜다.
    await hub.broadcast(channel_id, {"type": "ladder.state", "payload": state.model_dump()})


@router.post("/{channel_id}/ladder/join", response_model=LadderStateResponse)
async def join_ladder(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: LadderStore = Depends(get_ladder_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> LadderStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    await registry.acquire(channel_id, "ladder")
    game = await store.get_or_create(channel_id)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/ladder/participants", response_model=LadderStateResponse)
async def add_participant(
    channel_id: int,
    payload: AddLadderEntryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: LadderStore = Depends(get_ladder_store),
) -> LadderStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.add_participant(channel_id, payload.label, current_user.display_name)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.delete(
    "/{channel_id}/ladder/participants/{entry_id}", response_model=LadderStateResponse
)
async def remove_participant(
    channel_id: int,
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: LadderStore = Depends(get_ladder_store),
) -> LadderStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.remove_participant(channel_id, entry_id)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/ladder/results", response_model=LadderStateResponse)
async def add_result(
    channel_id: int,
    payload: AddLadderEntryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: LadderStore = Depends(get_ladder_store),
) -> LadderStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.add_result(channel_id, payload.label, current_user.display_name)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.delete("/{channel_id}/ladder/results/{entry_id}", response_model=LadderStateResponse)
async def remove_result(
    channel_id: int,
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: LadderStore = Depends(get_ladder_store),
) -> LadderStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.remove_result(channel_id, entry_id)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/ladder/run", response_model=LadderStateResponse)
async def run_ladder(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: LadderStore = Depends(get_ladder_store),
) -> LadderStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.run(channel_id, current_user.display_name)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/ladder/reset", response_model=LadderStateResponse)
async def reset_ladder(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: LadderStore = Depends(get_ladder_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> LadderStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.reset(channel_id)
    # 리셋은 이번 라운드를 접는다는 뜻 — 채널을 다른 게임에게 내준다.
    await registry.release(channel_id, "ladder")
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.get("/{channel_id}/ladder", response_model=LadderStateResponse)
async def get_ladder(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: LadderStore = Depends(get_ladder_store),
) -> LadderStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.get(channel_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 사다리가 없어요"
        )
    return _serialize(game)
