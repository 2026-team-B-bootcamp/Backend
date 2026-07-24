"""초성퀴즈(폭탄 돌리기) 게임 API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → ChosungStore(상태 저장/전이) →
ChosungStateResponse로 변환해 응답 + 웹소켓으로 전원에게 브로드캐스트.
초성 일치 검사 자체는 services/chosung/logic.py에 있다. 끝말잇기 라우터와
같은 구조지만 게임 레지스트리는 쓰지 않는다(각 게임이 채널에서 독립적으로 열림).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.chosung import (
    ChosungPlayerState,
    ChosungStateResponse,
    ChosungSubmitRequest,
)
from app.services import game_announce, server_service
from app.services.chosung.store import ChosungGame, ChosungStore, get_chosung_store
from app.services.realtime import hub

router = APIRouter(prefix="/channels", tags=["chosung"])


def _serialize(game: ChosungGame, store: ChosungStore) -> ChosungStateResponse:
    current = game.current_player()
    return ChosungStateResponse(
        status=game.status,
        round=game.round,
        players=[
            ChosungPlayerState(
                user_id=p.user_id, display_name=p.display_name, alive=p.alive
            )
            for p in game.players
        ],
        turn_user_id=current.user_id if current else None,
        prompt=game.prompt,
        words=list(game.words),
        loser_user_id=game.loser_user_id,
        seconds_left=store.seconds_left(game),
        last_event=game.last_event,
    )


async def _broadcast_state(channel_id: int, state: ChosungStateResponse) -> None:
    # 초성퀴즈 상태는 전원에게 동일한 공개 정보라 전체 상태를 그대로 쏜다.
    await hub.broadcast(
        channel_id, {"type": "chosung.state", "payload": state.model_dump()}
    )


@router.post("/{channel_id}/chosung/join", response_model=ChosungStateResponse)
async def join_chosung(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: ChosungStore = Depends(get_chosung_store),
) -> ChosungStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 게임이 없던 채널이면 이 참가가 새 판을 여는 것이다 — 채팅만 보고 있던
    # 사람에게도 보이도록 입장 카드를 남긴다. 참가한 뒤에 판정하면 이미 waiting
    # 상태라 "새로 열린 것"인지 "이미 있던 판에 낀 것"인지 구분할 수 없다.
    was_empty = await store.status(channel_id) == "none"
    game = await store.join(channel_id, current_user.id, current_user.display_name)
    state = _serialize(game, store)
    await _broadcast_state(channel_id, state)
    if was_empty:
        await game_announce.announce_opened(db, channel_id, current_user, "chosung")
    return state


@router.post("/{channel_id}/chosung/start", response_model=ChosungStateResponse)
async def start_chosung(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: ChosungStore = Depends(get_chosung_store),
) -> ChosungStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.start(channel_id, current_user.id)
    state = _serialize(game, store)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/chosung/submit", response_model=ChosungStateResponse)
async def submit_chosung(
    channel_id: int,
    payload: ChosungSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: ChosungStore = Depends(get_chosung_store),
) -> ChosungStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 초성 검증과 폭탄 넘기기는 store.submit 안에서 처리된다.
    game = await store.submit(channel_id, current_user.id, payload.word)
    state = _serialize(game, store)
    await _broadcast_state(channel_id, state)
    return state


@router.get("/{channel_id}/chosung", response_model=ChosungStateResponse)
async def get_chosung(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: ChosungStore = Depends(get_chosung_store),
) -> ChosungStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game, changed = await store.get(channel_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 게임이 없어요"
        )
    state = _serialize(game, store)
    if changed:
        # 도화선이 지연 판정으로 방금 터졌으면 모두에게 알린다.
        await _broadcast_state(channel_id, state)
    return state
