"""끝말잇기 게임 API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → WordChainStore(상태 저장/전이) →
WordChainStateResponse로 변환해 응답 + 웹소켓으로 전원에게 브로드캐스트.
단어 유효성 검사(끝말잇기 규칙) 자체는 services/wordchain/logic.py에 있다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.wordchain import (
    WordChainPlayerState,
    WordChainStateResponse,
    WordEntryOut,
    WordSubmitRequest,
)
from app.services import game_announce, server_service
from app.services.game_registry import GameRegistry, get_game_registry
from app.services.realtime import hub
from app.services.wordchain.store import (
    FINISHED,
    WordChainGame,
    WordChainStore,
    get_wordchain_store,
)

router = APIRouter(prefix="/channels", tags=["wordchain"])


def _serialize(game: WordChainGame, store: WordChainStore) -> WordChainStateResponse:
    current = game.current_player()
    return WordChainStateResponse(
        status=game.status,
        round=game.round,
        players=[
            WordChainPlayerState(
                user_id=p.user_id, display_name=p.display_name, alive=p.alive
            )
            for p in game.players
        ],
        turn_user_id=current.user_id if current else None,
        words=[
            WordEntryOut(user_id=w.user_id, display_name=w.display_name, word=w.word)
            for w in game.words
        ],
        loser_user_id=game.loser_user_id,
        seconds_left=store.seconds_left(game),
        last_event=game.last_event,
    )


async def _broadcast_state(channel_id: int, state: WordChainStateResponse) -> None:
    # 끝말잇기 상태는 전원에게 동일한 공개 정보라 전체 상태를 그대로 쏜다.
    await hub.broadcast(
        channel_id, {"type": "wordchain.state", "payload": state.model_dump()}
    )


@router.post("/{channel_id}/wordchain/join", response_model=WordChainStateResponse)
async def join_wordchain(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WordChainStore = Depends(get_wordchain_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> WordChainStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 이 채널에서 끝말잇기를 쓰겠다고 게임 레지스트리에 선점 등록 후, 대기실에 플레이어로 합류.
    await registry.acquire(channel_id, "wordchain")
    # 게임이 없던 채널이면 이 참가가 새 판을 여는 것이다 — 채팅만 보고 있던
    # 사람에게도 보이도록 입장 카드를 남긴다. 참가한 뒤에 판정하면 이미 waiting
    # 상태라 "새로 열린 것"인지 "이미 있던 판에 낀 것"인지 구분할 수 없다.
    was_empty = await store.status(channel_id) == "none"
    game = await store.join(channel_id, current_user.id, current_user.display_name)
    state = _serialize(game, store)
    await _broadcast_state(channel_id, state)
    if was_empty:
        await game_announce.announce_opened(db, channel_id, current_user, "wordchain")
    return state


@router.post("/{channel_id}/wordchain/start", response_model=WordChainStateResponse)
async def start_wordchain(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WordChainStore = Depends(get_wordchain_store),
) -> WordChainStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.start(channel_id, current_user.id)
    state = _serialize(game, store)
    await _broadcast_state(channel_id, state)
    return state


@router.post("/{channel_id}/wordchain/submit", response_model=WordChainStateResponse)
async def submit_word(
    channel_id: int,
    payload: WordSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WordChainStore = Depends(get_wordchain_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> WordChainStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    # 단어 검증(끝글자 잇기, 중복 여부 등)과 다음 차례 전환은 store.submit 안에서 처리된다.
    game = await store.submit(channel_id, current_user.id, payload.word)
    if game.status == FINISHED:
        # 게임이 끝나면 채널을 다른 게임에게 내준다.
        await registry.release(channel_id, "wordchain")
    state = _serialize(game, store)
    await _broadcast_state(channel_id, state)
    return state


@router.get("/{channel_id}/wordchain", response_model=WordChainStateResponse)
async def get_wordchain(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: WordChainStore = Depends(get_wordchain_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> WordChainStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game, changed = await store.get(channel_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 게임이 없어요"
        )
    state = _serialize(game, store)
    if changed:
        # 타임아웃이 지연 판정으로 방금 반영됐으면 모두에게 알린다.
        if game.status == FINISHED:
            await registry.release(channel_id, "wordchain")
        await _broadcast_state(channel_id, state)
    return state
