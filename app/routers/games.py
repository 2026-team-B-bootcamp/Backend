"""채널의 미니게임들 현재 상태를 한 번에 알려주는 라우터.

각 게임 종류가 채널에서 독립적으로 열리므로(레지스트리 폐기), 관전 유도를 위해
게임별 상태(none/waiting/playing/finished)를 모아 돌려준다. 프론트가 게임 선택
목록에 🙂 대기 / 🟢 진행중 / 🚩 종료 뱃지를 붙이는 데 쓴다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.services import server_service
from app.services.balance.store import get_balance_store
from app.services.bingo.store import get_bingo_store
from app.services.chosung.store import get_chosung_store
from app.services.omok.store import get_omok_store
from app.services.tictactoe.store import get_tictactoe_store
from app.services.wordchain.store import get_wordchain_store

router = APIRouter(prefix="/channels", tags=["games"])


@router.get("/{channel_id}/games/status", response_model=dict[str, str])
async def games_status(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await server_service.require_channel_access(db, channel_id, current_user.id)
    return {
        "bingo": await get_bingo_store().status(channel_id),
        "wordchain": await get_wordchain_store().status(channel_id),
        "omok": await get_omok_store().status(channel_id),
        "tictactoe": await get_tictactoe_store().status(channel_id),
        "balance": await get_balance_store().status(channel_id),
        "chosung": await get_chosung_store().status(channel_id),
    }
