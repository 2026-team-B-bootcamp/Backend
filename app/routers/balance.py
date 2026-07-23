"""밸런스게임(게시글형 토론 + 제한시간) API 라우터.

요청 흐름: 클라이언트 → 이 라우터 → game_registry(채널 게임 잠금) → balance store
→ 상태 직렬화 응답 + realtime hub로 브로드캐스트.
제한시간이 끝나면(finished) 조회 시 registry 잠금을 느슨하게 해제해(idempotent) 다른 게임이
열릴 수 있게 한다. 개인별 투표(my_vote)는 브로드캐스트엔 싣지 않고,
각 클라이언트가 자기 값을 유지한다.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.balance import (
    BalanceComment as BalanceCommentSchema,
)
from app.schemas.balance import (
    BalanceStateResponse,
    CommentBalanceRequest,
    StartBalanceRequest,
    VoteBalanceRequest,
)
from app.services import server_service
from app.services.balance.store import BalanceGame, BalanceStore, get_balance_store
from app.services.game_registry import GameRegistry, get_game_registry
from app.services.realtime import hub

router = APIRouter(prefix="/channels", tags=["balance"])

_INACTIVE = BalanceStateResponse(active=False)


def _serialize(game: BalanceGame, user_id: int | None, now: float) -> BalanceStateResponse:
    values = game.votes.values()
    return BalanceStateResponse(
        active=True,
        option_a=game.option_a,
        option_b=game.option_b,
        count_a=sum(1 for v in values if v == "a"),
        count_b=sum(1 for v in values if v == "b"),
        my_vote=game.votes.get(str(user_id)) if user_id is not None else None,
        comments=[
            BalanceCommentSchema(
                user_id=c.user_id, display_name=c.display_name, side=c.side, text=c.text
            )
            for c in game.comments
        ],
        ends_at=game.ends_at(),
        finished=game.is_finished(now),
        host_user_id=game.host_user_id,
        host_name=game.host_name,
    )


async def _broadcast(channel_id: int, game: BalanceGame, now: float) -> None:
    # 브로드캐스트엔 개인 투표(my_vote)를 싣지 않는다 — 각 클라이언트가 자기 값을 유지한다.
    payload = _serialize(game, None, now).model_dump()
    await hub.broadcast(channel_id, {"type": "balance.state", "payload": payload})


@router.post("/{channel_id}/balance/start", response_model=BalanceStateResponse)
async def start_balance(
    channel_id: int,
    payload: StartBalanceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: BalanceStore = Depends(get_balance_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> BalanceStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    await registry.acquire(channel_id, "balance")
    game = await store.start(
        channel_id, payload.option_a, payload.option_b, current_user.id, current_user.display_name
    )
    now = time.time()
    await _broadcast(channel_id, game, now)
    return _serialize(game, current_user.id, now)


@router.post("/{channel_id}/balance/vote", response_model=BalanceStateResponse)
async def vote_balance(
    channel_id: int,
    payload: VoteBalanceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: BalanceStore = Depends(get_balance_store),
) -> BalanceStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.vote(channel_id, current_user.id, payload.side)
    now = time.time()
    await _broadcast(channel_id, game, now)
    return _serialize(game, current_user.id, now)


@router.post("/{channel_id}/balance/comment", response_model=BalanceStateResponse)
async def comment_balance(
    channel_id: int,
    payload: CommentBalanceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: BalanceStore = Depends(get_balance_store),
) -> BalanceStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.comment(
        channel_id, current_user.id, current_user.display_name, payload.text
    )
    now = time.time()
    await _broadcast(channel_id, game, now)
    return _serialize(game, current_user.id, now)


@router.post("/{channel_id}/balance/reset", response_model=BalanceStateResponse)
async def reset_balance(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: BalanceStore = Depends(get_balance_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> BalanceStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    await store.reset(channel_id)
    await registry.release(channel_id, "balance")
    await hub.broadcast(channel_id, {"type": "balance.state", "payload": _INACTIVE.model_dump()})
    return _INACTIVE


@router.get("/{channel_id}/balance", response_model=BalanceStateResponse)
async def get_balance(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    store: BalanceStore = Depends(get_balance_store),
    registry: GameRegistry = Depends(get_game_registry),
) -> BalanceStateResponse:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    game = await store.get(channel_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 게임이 없어요"
        )
    now = time.time()
    # 제한시간이 끝났으면 채널 게임 잠금을 풀어 다른 게임이 열릴 수 있게 한다(idempotent).
    if game.is_finished(now):
        await registry.release(channel_id, "balance")
    return _serialize(game, current_user.id, now)
