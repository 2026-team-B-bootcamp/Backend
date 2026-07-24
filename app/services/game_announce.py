"""게임이 새로 열리면 채팅에 "입장하기" 카드를 남긴다.

문제: 게임은 채팅 위에 떠 있는 PIP나 전용 화면에서 열리는데, 그 순간 채팅만
보고 있던 사람에게는 아무 신호가 없었다. 헤더 아이콘의 작은 점 하나가 전부라
"누가 빙고 열었네" 하고 알아채기 어려웠다.

해결: 게임이 없던 채널에 누가 처음 들어오면 채팅 흐름에 카드를 하나 남긴다.
환영 카드(kind="welcome")와 같은 구조라, 프런트는 kind만 보고 다르게 그린다.

카드 내용(content)에는 게임 키만 담는다. 문구를 서버에서 만들어 넣으면
나중에 표현을 바꿀 때 이미 쌓인 메시지는 옛 문구로 남는다. 키만 두면 프런트가
그릴 때마다 현재 문구로 그린다.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.models.message import KIND_GAME
from app.models.user import User
from app.schemas.message import MessageOut
from app.services import message_service
from app.services.realtime import hub

logger = logging.getLogger(__name__)

# 같은 순간에 두 명이 동시에 들어와 카드가 두 장 생기는 것만 막으면 되므로 짧게 잡는다.
# 게임이 끝나고 한참 뒤 다시 열리면 그때는 새 카드가 나와야 한다.
_DEDUPE_SECONDS = 60


async def announce_opened(
    db: AsyncSession, channel_id: int, opener: User, game_key: str
) -> None:
    """게임이 새로 열렸다는 카드를 채팅에 남긴다.

    호출부는 "직전 상태가 none이었는가"로 새 게임인지 판단한다. 그 판정과 이
    함수 사이에 다른 요청이 끼어들 수 있어, 여기서 한 번 더 선점 검사를 한다.
    """
    key = f"game:announced:{channel_id}:{game_key}"
    try:
        first = await get_redis().set(key, "1", nx=True, ex=_DEDUPE_SECONDS)
    except Exception:
        # Redis가 순단해도 게임은 계속돼야 한다. 카드가 두 장 생길 위험만 감수한다.
        logger.warning("게임 카드 중복 검사 실패 — 그대로 진행", exc_info=True)
        first = True
    if not first:
        return

    try:
        message, display_name, avatar_url = await message_service.create_message(
            db, channel_id, opener.id, game_key, kind=KIND_GAME
        )
    except Exception:
        # 카드를 못 남겨도 게임 참가는 성공해야 한다 — 알림은 부가 기능이다.
        logger.exception("게임 카드 생성 실패 channel_id=%s game=%s", channel_id, game_key)
        return

    out = MessageOut(
        id=message.id,
        user_id=message.user_id,
        display_name=display_name,
        avatar_url=avatar_url,
        tags=[],
        content=message.content,
        kind=message.kind,
        created_at=message.created_at,
    )
    try:
        await hub.broadcast(
            channel_id, {"type": "message.new", "payload": out.model_dump(mode="json")}
        )
    except Exception:
        # 이미 DB에 있으므로 다른 사람은 새로고침·재연결 시 보게 된다.
        logger.exception("게임 카드 broadcast 실패 channel_id=%s", channel_id)
