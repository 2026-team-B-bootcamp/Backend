"""빙고 미니게임 API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → game_registry(채널당 게임 종류 잠금)
→ bingo store(게임 상태 저장/전이) + bingo logic(줄 완성 판정) → 결과를
직렬화해 응답하고 realtime hub로 채널에 변경을 알린다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.bingo import BingoStateResponse, ClickRequest, PlayerState
from app.services import server_service
from app.services.bingo.logic import count_completed_lines
from app.services.bingo.store import BingoGame, BingoGameStore, get_bingo_store
from app.services.game_registry import GameRegistry, get_game_registry
from app.services.realtime import hub

router = APIRouter(prefix="/channels", tags=["bingo"])


def _serialize(game: BingoGame, requester_id: int) -> BingoStateResponse:
    # my_board is only ever the requester's own board — never another player's.
    my_player = game.players.get(requester_id)
    players = [
        PlayerState(
            user_id=pid,
            display_name=player.display_name,
            completed_lines=count_completed_lines(player.board, game.called_numbers),
        )
        for pid, player in game.players.items()
    ]
    return BingoStateResponse(
        called_numbers=sorted(game.called_numbers),
        my_board=my_player.board if my_player else None,
        players=players,
        winner_user_id=game.winner_user_id,
        round=game.round,
    )


async def _notify(channel_id: int) -> None:
    # Boards are per-player secrets, so only a "something changed" ping is
    # broadcast; each client refetches its own view over REST.
    await hub.broadcast(channel_id, {"type": "bingo.update", "payload": {}})


@router.post("/{channel_id}/bingo/join", response_model=BingoStateResponse)
async def join_bingo(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: BingoGameStore = Depends(get_bingo_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> BingoStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 채널을 빙고 게임으로 점유(다른 게임과 동시 진행 방지)한 뒤 참가 처리.
    await registry.acquire(channel_id, "bingo")
    game = await store.join(channel_id, current_user.id, current_user.display_name)
    await _notify(channel_id)
    return _serialize(game, current_user.id)


@router.post("/{channel_id}/bingo/click", response_model=BingoStateResponse)
async def click_bingo(
    channel_id: int,
    payload: ClickRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: BingoGameStore = Depends(get_bingo_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> BingoStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 번호 하나를 호출 처리하고, store 내부에서 승리(3줄 완성) 여부까지 판정한다.
    game = await store.click(channel_id, current_user.id, payload.number)
    if game.winner_user_id is not None:
        # 라운드가 끝나면 채널을 다른 게임에게 내준다 — 재참여하면 새 라운드로 다시 잠긴다.
        await registry.release(channel_id, "bingo")
    await _notify(channel_id)
    return _serialize(game, current_user.id)


@router.get("/{channel_id}/bingo", response_model=BingoStateResponse)
async def get_bingo(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: BingoGameStore = Depends(get_bingo_store),
) -> BingoStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.get(channel_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No active bingo game"
        )
    return _serialize(game, current_user.id)
