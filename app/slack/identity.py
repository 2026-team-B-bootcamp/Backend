"""슬랙 신원 ↔ Deverapo 계정 매핑, 입장 링크 발급.

슬랙 유저/채널을 Deverapo DB의 계정·채널로 미러링하고(`mirror.ensure_context`),
그 결과로 개인 입장 링크를 만들거나(`_issue_entry_link`) 태그를 읽어오는
(`_load_tags`) 등 DB·슬랙 API를 오가는 함수들을 모은다.
"""

import logging

from app.db import base as db_base
from app.services import tag_service
from app.slack import mirror
from app.slack.features import Feature
from app.slack.link_token import build_entry_link

logger = logging.getLogger(__name__)


async def _display_name(client, user_id: str, fallback: str) -> str:
    """슬랙 표시 이름을 가져온다. 실패해도 흐름을 막지 않는다.

    이름 하나 때문에 입장이 실패하면 안 되므로, 조회가 안 되면 페이로드에
    들어있는 핸들을 그대로 쓴다. 이메일 등 다른 프로필 정보는 읽지 않는다.
    """
    try:
        res = await client.users_info(user=user_id)
        profile = res.get("user", {}).get("profile", {})
        name = profile.get("display_name") or profile.get("real_name")
        if name:
            return name[:100]
    except Exception:
        logger.warning("users.info 조회 실패 — 페이로드의 이름을 쓴다", exc_info=True)
    return (fallback or user_id)[:100]


async def _issue_entry_link(client, body: dict, feature: Feature) -> str:
    """버튼을 누른 사람에게 줄 개인 링크를 만든다.

    슬랙 신원을 Deverapo 계정으로 미러링하고(없으면 게스트 생성), 그 계정으로
    로그인된 짧은 수명의 링크를 발급한다.
    """
    team = body.get("team") or {}
    channel = body.get("channel") or {}
    user = body.get("user") or {}
    slack_user_id = user.get("id", "")

    display_name = await _display_name(
        client, slack_user_id, user.get("username") or user.get("name", "")
    )

    # 라우터가 아니라서 Depends(get_db)를 못 쓴다. 모듈 속성으로 접근하는 이유는
    # 테스트가 여기를 테스트 DB로 바꿔칠 수 있게 하기 위함이다(core/redis.py와 같은 방식).
    async with db_base.async_session_maker() as db:
        ieum_user, ieum_channel = await mirror.ensure_context(
            db,
            team_id=team.get("id", ""),
            team_name=team.get("domain"),
            slack_channel_id=channel.get("id", ""),
            channel_name=channel.get("name") or "슬랙",
            slack_user_id=slack_user_id,
            display_name=display_name,
        )
        # 미러링 결과를 확정한 뒤에 토큰을 만든다 — 커밋 전 id로 링크를 주면
        # 롤백 시 존재하지 않는 유저를 가리키는 링크가 나간다.
        await db.commit()
        return build_entry_link(ieum_user, ieum_channel, feature)


async def _resolve_target(client, team: dict, channel: dict, slack_user_id: str):
    """슬랙 유저 하나를 Deverapo 계정·채널로 옮긴다.

    대상이 아직 Deverapo 계정이 없을 수도 있다(봇을 한 번도 안 쓴 사람). 그 경우에도
    게스트를 만들어 매핑해둔다 — 그래야 그 사람 태그를 나중에 붙일 자리가 생긴다.
    """
    display_name = await _display_name(client, slack_user_id, "")
    async with db_base.async_session_maker() as db:
        user, ieum_channel = await mirror.ensure_context(
            db,
            team_id=team.get("id", ""),
            team_name=team.get("domain"),
            slack_channel_id=channel.get("id", ""),
            channel_name=channel.get("name") or "슬랙",
            slack_user_id=slack_user_id,
            display_name=display_name,
        )
        await db.commit()
        return user.id, user.display_name, ieum_channel.server_id


async def _load_tags(server_id: int, user_id: int) -> list[str]:
    async with db_base.async_session_maker() as db:
        tag = await tag_service.get_user_tags(db, server_id, user_id)
        if tag is None:
            return []
        return [t for t in tag_service.tag_values(tag) if t and t.strip()]
