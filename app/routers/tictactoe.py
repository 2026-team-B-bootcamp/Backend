"""틱택토(3×3, 1:1) 미니게임 API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → game_registry(채널당 게임 종류 잠금)
→ tictactoe store → 상태 직렬화 응답 + realtime hub로 채널 전체에 브로드캐스트.
오목 라우터와 동일한 구조다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.tictactoe import (
    TicTacToePlaceRequest,
    TicTacToePlayerState,
    TicTacToeStateResponse,
)
from app.services import server_service
from app.services.game_registry import GameRegistry, get_game_registry
from app.services.realtime import hub
from app.services.tictactoe.store import (
    PLAYING,
    TicTacToeGame,
    TicTacToeStore,
    get_tictactoe_store,
)

router = APIRouter(prefix="/channels", tags=["tictactoe"])


def _serialize(game: TicTacToeGame) -> TicTacToeStateResponse:
    current = game.current_player()
    return TicTacToeStateResponse(
        status=game.status,
        board=game.board,
        players=[
            TicTacToePlayerState(user_id=p.user_id, display_name=p.display_name, mark=p.mark)
            for p in game.players
        ],
        turn=game.turn if game.status == PLAYING else None,
        turn_user_id=current.user_id if current else None,
        winner_user_id=game.winner_user_id,
        winning_line=game.winning_line,
        last_move=game.last_move,
    )


async def _broadcast_state(channel_id: int, state: TicTacToeStateResponse) -> None:
    await hub.broadcast(channel_id, {"type": "tictactoe.state", "payload": state.model_dump()})


@router.post("/{channel_id}/tictactoe/join", response_model=TicTacToeStateResponse)
async def join_tictactoe(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: TicTacToeStore = Depends(get_tictactoe_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> TicTacToeStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    await registry.acquire(channel_id, "tictactoe")
    game = await store.join(channel_id, current_user.id, current_user.display_name)
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/tictactoe/place", response_model=TicTacToeStateResponse)
async def place_mark(
    channel_id: int,
    payload: TicTacToePlaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: TicTacToeStore = Depends(get_tictactoe_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> TicTacToeStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.place(channel_id, current_user.id, payload.row, payload.col)
    if game.status != PLAYING:
        await registry.release(channel_id, "tictactoe")
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/tictactoe/reset", response_model=TicTacToeStateResponse)
async def reset_tictactoe(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: TicTacToeStore = Depends(get_tictactoe_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> TicTacToeStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.reset(channel_id)
    await registry.release(channel_id, "tictactoe")
    state = _serialize(game)
    await _broadcast_state(channel_id, state)
    return state


@router.get("/{channel_id}/tictactoe", response_model=TicTacToeStateResponse)
async def get_tictactoe(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: TicTacToeStore = Depends(get_tictactoe_store),
) -> TicTacToeStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.get(channel_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 게임이 없어요"
        )
    return _serialize(game)
