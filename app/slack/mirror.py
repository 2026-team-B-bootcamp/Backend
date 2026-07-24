"""슬랙 team/channel/user → 이음 server/channel/user 매핑.

슬랙에서 온 요청을 기존 서비스 계층이 그대로 쓸 수 있는 이음 엔티티로 바꾼다.
게임·태그·채팅 로직은 이 층 아래에서 슬랙의 존재를 전혀 모른다.

**모든 함수는 멱등이다.** 같은 슬랙 계정이 버튼을 몇 번 누르든 항상 같은
이음 user_id로 수렴한다. 동시에 두 번 눌러도 마찬가지인데, 이는 애플리케이션의
"있으면 재사용, 없으면 생성" 분기가 아니라 DB UNIQUE 제약이 보장한다 —
분기 사이에 다른 요청이 끼어드는 경합을 코드로는 막을 수 없기 때문이다.

커밋하지 않는다. 호출부(핸들러)가 한 요청을 한 트랜잭션으로 묶어 커밋한다.
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel
from app.models.platform_identity import PlatformIdentity, SlackChannel, SlackWorkspace
from app.models.server import Server, ServerMember
from app.models.user import User
from app.services.server_service import generate_invite_code

logger = logging.getLogger(__name__)

PLATFORM_SLACK = "slack"


class _LostRace(Exception):
    """동시 요청에 밀렸다는 내부 신호. 밖으로 새지 않는다."""


async def _allocate_invite_code(db: AsyncSession) -> str:
    """다른 서버와 겹치지 않는 초대 코드를 뽑는다."""
    for _ in range(10):
        code = generate_invite_code()
        if await db.scalar(select(Server).where(Server.invite_code == code)) is None:
            return code
    raise RuntimeError("초대 코드를 할당하지 못했습니다")


async def ensure_identity(
    db: AsyncSession,
    *,
    team_id: str,
    slack_user_id: str,
    display_name: str,
) -> PlatformIdentity:
    """슬랙 계정에 대응하는 이음 계정을 찾거나 만든다.

    없으면 이메일·비밀번호가 없는 게스트 User를 새로 만들어 연결한다.
    슬랙에서 가져오는 개인정보는 표시 이름 하나뿐이다(이메일·프로필 조회 안 함).
    """
    existing = await _select_identity(db, team_id=team_id, slack_user_id=slack_user_id)
    if existing is not None:
        # 슬랙에서 이름을 바꿨으면 플랫폼 쪽 이름만 따라간다. 이음 User.display_name은
        # 웹에서 본인이 고쳤을 수 있으므로 덮어쓰지 않는다.
        if existing.display_name != display_name:
            existing.display_name = display_name
        return existing

    try:
        # 게스트 생성과 매핑 INSERT를 SAVEPOINT로 묶는다. 경합에서 지면 통째로
        # 되돌려, 아무 매핑에도 연결되지 않은 고아 게스트가 남지 않게 한다.
        async with db.begin_nested():
            guest = User(display_name=display_name, is_guest=True)
            db.add(guest)
            await db.flush()

            inserted_id = await db.scalar(
                pg_insert(PlatformIdentity)
                .values(
                    user_id=guest.id,
                    platform=PLATFORM_SLACK,
                    platform_team=team_id,
                    platform_user_id=slack_user_id,
                    display_name=display_name,
                )
                .on_conflict_do_nothing(constraint="uq_identity")
                .returning(PlatformIdentity.id)
            )
            if inserted_id is None:
                # 우리가 조회한 뒤~INSERT 사이에 다른 요청이 먼저 만들었다.
                raise _LostRace
    except _LostRace:
        logger.info("동시 요청에 밀림 — 기존 매핑을 재사용한다 (user=%s)", slack_user_id)
        identity = await _select_identity(db, team_id=team_id, slack_user_id=slack_user_id)
        if identity is None:  # pragma: no cover - UNIQUE 충돌 뒤엔 반드시 존재한다
            raise RuntimeError("매핑 충돌 후 조회에 실패했습니다") from None
        return identity

    identity = await _select_identity(db, team_id=team_id, slack_user_id=slack_user_id)
    assert identity is not None  # noqa: S101 - 방금 INSERT가 성공했다
    return identity


async def _select_identity(
    db: AsyncSession, *, team_id: str, slack_user_id: str
) -> PlatformIdentity | None:
    return await db.scalar(
        select(PlatformIdentity).where(
            PlatformIdentity.platform == PLATFORM_SLACK,
            PlatformIdentity.platform_team == team_id,
            PlatformIdentity.platform_user_id == slack_user_id,
        )
    )


async def ensure_workspace(
    db: AsyncSession,
    *,
    team_id: str,
    team_name: str | None,
    creator_user_id: int,
) -> SlackWorkspace:
    """슬랙 워크스페이스에 대응하는 이음 서버를 찾거나 만든다.

    `creator_user_id`는 Server.created_by(NOT NULL)를 채우기 위한 값으로,
    워크스페이스를 처음 건드린 사람이 들어간다. 소유권 의미는 없다.
    """
    existing = await db.scalar(select(SlackWorkspace).where(SlackWorkspace.team_id == team_id))
    if existing is not None:
        return existing

    try:
        async with db.begin_nested():
            server = Server(
                name=team_name or f"슬랙 워크스페이스 {team_id}",
                invite_code=await _allocate_invite_code(db),
                created_by=creator_user_id,
            )
            db.add(server)
            await db.flush()
            # 기본 채널("일반")은 만들지 않는다 — 슬랙 채널이 1:1로 미러링되므로
            # 대응하는 슬랙 채널이 없는 빈 채널이 생기면 혼란만 준다.

            inserted_id = await db.scalar(
                pg_insert(SlackWorkspace)
                .values(team_id=team_id, team_name=team_name, server_id=server.id)
                .on_conflict_do_nothing(index_elements=["team_id"])
                .returning(SlackWorkspace.id)
            )
            if inserted_id is None:
                raise _LostRace
    except _LostRace:
        workspace = await db.scalar(
            select(SlackWorkspace).where(SlackWorkspace.team_id == team_id)
        )
        if workspace is None:  # pragma: no cover
            raise RuntimeError("워크스페이스 충돌 후 조회에 실패했습니다") from None
        return workspace

    workspace = await db.scalar(select(SlackWorkspace).where(SlackWorkspace.team_id == team_id))
    assert workspace is not None  # noqa: S101
    return workspace


async def ensure_channel(
    db: AsyncSession,
    *,
    team_id: str,
    slack_channel_id: str,
    channel_name: str,
    server_id: int,
) -> SlackChannel:
    """슬랙 채널에 대응하는 이음 채널을 찾거나 만든다."""
    existing = await _select_channel(
        db, team_id=team_id, slack_channel_id=slack_channel_id
    )
    if existing is not None:
        return existing

    try:
        async with db.begin_nested():
            channel = Channel(server_id=server_id, name=channel_name)
            db.add(channel)
            await db.flush()

            inserted_id = await db.scalar(
                pg_insert(SlackChannel)
                .values(
                    team_id=team_id,
                    slack_channel_id=slack_channel_id,
                    channel_id=channel.id,
                )
                .on_conflict_do_nothing(constraint="uq_slack_channel")
                .returning(SlackChannel.id)
            )
            if inserted_id is None:
                raise _LostRace
    except _LostRace:
        mapping = await _select_channel(db, team_id=team_id, slack_channel_id=slack_channel_id)
        if mapping is None:  # pragma: no cover
            raise RuntimeError("채널 충돌 후 조회에 실패했습니다") from None
        return mapping

    mapping = await _select_channel(db, team_id=team_id, slack_channel_id=slack_channel_id)
    assert mapping is not None  # noqa: S101
    return mapping


async def _select_channel(
    db: AsyncSession, *, team_id: str, slack_channel_id: str
) -> SlackChannel | None:
    return await db.scalar(
        select(SlackChannel).where(
            SlackChannel.team_id == team_id,
            SlackChannel.slack_channel_id == slack_channel_id,
        )
    )


async def ensure_membership(db: AsyncSession, *, server_id: int, user_id: int) -> None:
    """유저를 서버 멤버로 등록한다. 이미 멤버면 아무 일도 하지 않는다.

    멤버십이 없으면 기존 권한 검사(server_service.require_membership)에 막혀
    슬랙에서 만든 링크로 들어와도 채널이 안 보인다.
    """
    # uq_member_server_user 제약이 있어 ON CONFLICT로 한 방에 끝난다 —
    # 조회 후 INSERT와 달리 경합에서도 예외가 나지 않는다.
    await db.execute(
        pg_insert(ServerMember)
        .values(server_id=server_id, user_id=user_id)
        .on_conflict_do_nothing(constraint="uq_member_server_user")
    )


async def ensure_context(
    db: AsyncSession,
    *,
    team_id: str,
    team_name: str | None,
    slack_channel_id: str,
    channel_name: str,
    slack_user_id: str,
    display_name: str,
) -> tuple[User, Channel]:
    """슬랙 요청 하나를 이음의 (유저, 채널)로 통째로 번역한다.

    핸들러가 실제로 쓰는 진입점 — 위 함수들을 올바른 순서로 엮어준다.
    identity를 먼저 만드는 이유는 Server.created_by에 넣을 user_id가 필요해서다.
    """
    identity = await ensure_identity(
        db, team_id=team_id, slack_user_id=slack_user_id, display_name=display_name
    )
    workspace = await ensure_workspace(
        db, team_id=team_id, team_name=team_name, creator_user_id=identity.user_id
    )
    mapping = await ensure_channel(
        db,
        team_id=team_id,
        slack_channel_id=slack_channel_id,
        channel_name=channel_name,
        server_id=workspace.server_id,
    )
    await ensure_membership(db, server_id=workspace.server_id, user_id=identity.user_id)

    user = await db.get(User, identity.user_id)
    channel = await db.get(Channel, mapping.channel_id)
    assert user is not None and channel is not None  # noqa: S101 - FK가 보장한다
    return user, channel
