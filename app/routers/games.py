"""채널의 미니게임들을 종류에 상관없이 한곳에서 다루는 라우터.

두 가지를 담당한다.
① 현재 상태 모아보기(GET .../games/status) — 각 게임 종류가 채널에서 독립적으로
   열리므로, 관전 유도를 위해 게임별 상태(none/waiting/playing/finished)를 모아
   돌려준다. 프론트가 게임 선택 목록에 🙂 대기 / 🟢 진행중 / 🚩 종료 뱃지를 붙이는 데 쓴다.
② 방장의 강제 종료(POST .../games/{kind}/end) — 판을 연 사람이 언제든 판을 접을 수 있다.

강제 종료를 게임별 라우터에 6번 복붙하지 않고 여기 하나로 둔 이유: 하는 일이
"방장인지 확인하고 Redis 키를 지운다"뿐이라 게임마다 다를 게 없다. 각 store는
host()/clear() 두 메서드만 제공하면 되고, 게임이 하나 늘면 아래 표에 한 줄만 더한다.
"""

import logging
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.core.redis import get_redis
from app.models.user import User
from app.services import server_service
from app.services.balance.store import get_balance_store
from app.services.bingo.store import get_bingo_store
from app.services.chosung.store import get_chosung_store
from app.services.omok.store import get_omok_store
from app.services.realtime import hub
from app.services.tictactoe.store import get_tictactoe_store
from app.services.wordchain.store import get_wordchain_store

router = APIRouter(prefix="/channels", tags=["games"])
logger = logging.getLogger(__name__)


class GameStore(Protocol):
    """게임 종류에 상관없이 이 라우터가 필요로 하는 최소 인터페이스."""

    async def status(self, channel_id: int) -> str: ...
    async def host(self, channel_id: int) -> int | None: ...
    async def clear(self, channel_id: int) -> None: ...


# 게임 키 → store 팩토리. 상태 모아보기와 강제 종료가 같은 표를 읽는다.
_STORES: dict[str, object] = {
    "bingo": get_bingo_store,
    "wordchain": get_wordchain_store,
    "omok": get_omok_store,
    "tictactoe": get_tictactoe_store,
    "balance": get_balance_store,
    "chosung": get_chosung_store,
}


def _store(kind: str) -> GameStore:
    factory = _STORES.get(kind)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="알 수 없는 게임이에요"
        )
    return factory()  # type: ignore[operator]


@router.get("/{channel_id}/games/status", response_model=dict[str, str])
async def games_status(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    return {kind: await _store(kind).status(channel_id) for kind in _STORES}


@router.post("/{channel_id}/games/{kind}/end", response_model=dict[str, str])
async def end_game(
    channel_id: int,
    kind: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """방장이 진행 중인 판을 강제로 접는다.

    판을 FINISHED로 바꾸는 대신 키를 통째로 지운다 — TTL 만료로 사라질 때와
    똑같이 "게임 없음" 상태로 돌아가야 다음 사람이 곧바로 새 판을 열 수 있다.
    """
    await server_service.require_channel_access(db, channel_id, current_user.id)
    store = _store(kind)

    host_id = await store.host(channel_id)
    if host_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 게임이 없어요"
        )
    if host_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="게임을 연 사람만 종료할 수 있어요",
        )

    await store.clear(channel_id)
    # 게임이 새로 열릴 때 채팅에 남기는 카드의 중복 방지 키를 함께 지운다.
    # 이게 남아 있으면 방금 접은 게임을 곧바로 다시 열어도 카드가 안 나온다.
    try:
        await get_redis().delete(f"game:announced:{channel_id}:{kind}")
    except Exception:
        # 카드 한 장 못 남기는 문제라 강제 종료 자체를 실패시킬 이유는 없다.
        logger.warning("게임 카드 dedupe 키 삭제 실패", exc_info=True)

    # 각 패널은 자기 게임의 "…state" 이벤트만 듣는데, 판이 사라진 상태에는
    # 실어 보낼 state가 없다. 그래서 종류를 담은 공통 이벤트를 하나 쏘고,
    # 패널들은 자기 kind일 때 "게임 없음"으로 되돌린다.
    await hub.broadcast(
        channel_id,
        {
            "type": "game.ended",
            "payload": {
                "kind": kind,
                "by_user_id": current_user.id,
                "by_name": current_user.display_name,
            },
        },
    )
    return {"status": "ended"}
