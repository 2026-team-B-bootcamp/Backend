"""오목 미니게임 API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → game_registry(채널당 게임 종류 잠금)
→ omok store(게임 상태 저장, 턴 전이, 오목 판정 호출) → 상태를 직렬화해
응답하고 realtime hub로 채널 전체에 최신 판을 브로드캐스트한다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.omok import OmokPlaceRequest, OmokPlayerState, OmokStateResponse
from app.services import game_announce, server_service
from app.services.game_registry import GameRegistry, get_game_registry
from app.services.omok.store import PLAYING, OmokGame, OmokStore, get_omok_store
from app.services.realtime import hub

router = APIRouter(prefix="/channels", tags=["omok"])


def _serialize(game: OmokGame) -> OmokStateResponse:
    current = game.current_player()
    return OmokStateResponse(
        status=game.status,
        board=game.board,
        players=[
            OmokPlayerState(user_id=p.user_id, display_name=p.display_name, color=p.color)
            for p in game.players
        ],
        turn=game.turn if game.status == PLAYING else None,
        turn_user_id=current.user_id if current else None,
        winner_user_id=game.winner_user_id,
        winning_line=game.winning_line,
        last_move=game.last_move,
    )


async def _broadcast_state(channel_id: int, state: OmokStateResponse) -> None:
    # 오목 판은 전원에게 동일한 공개 정보라 전체 상태를 그대로 쏜다.
    await hub.broadcast(channel_id, {"type": "omok.state", "payload": state.model_dump()})


@router.post("/{channel_id}/omok/join", response_model=OmokStateResponse)
async def join_omok(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: OmokStore = Depends(get_omok_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> OmokStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 채널을 오목 게임으로 점유한 뒤 참가시킨다 (2명이 되면 store가 자동으로 대국 시작).
    await registry.acquire(channel_id, "omok")
    # 게임이 없던 채널이면 이 참가가 새 판을 여는 것이다 — 채팅만 보고 있던
    # 사람에게도 보이도록 입장 카드를 남긴다. 참가한 뒤에 판정하면 이미 waiting
    # 상태라 "새로 열린 것"인지 "이미 있던 판에 낀 것"인지 구분할 수 없다.
    was_empty = await store.status(channel_id) == "none"
    game = await store.join(channel_id, current_user.id, current_user.display_name)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    if was_empty:
        await game_announce.announce_opened(db, channel_id, current_user, "omok")
    return state


@router.post("/{channel_id}/omok/place", response_model=OmokStateResponse)
async def place_stone(
    channel_id: int,
    payload: OmokPlaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: OmokStore = Depends(get_omok_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> OmokStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 돌을 놓고, store 내부에서 5목 완성/판 다 참 여부까지 판정해 상태를 갱신한다.
    game = await store.place(channel_id, current_user.id, payload.row, payload.col)
    if game.status != PLAYING:
        # 승부가 나면 채널을 다른 게임에게 내준다.
        await registry.release(channel_id, "omok")
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/omok/reset", response_model=OmokStateResponse)
async def reset_omok(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: OmokStore = Depends(get_omok_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> OmokStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.reset(channel_id)
    await registry.release(channel_id, "omok")
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.get("/{channel_id}/omok", response_model=OmokStateResponse)
async def get_omok(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: OmokStore = Depends(get_omok_store),
) -> OmokStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.get(channel_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 게임이 없어요"
        )
    return _serialize(game)
