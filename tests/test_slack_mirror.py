"""슬랙 ↔ 이음 매핑(app/slack/mirror.py) 테스트.

핵심 불변식은 하나다: **같은 슬랙 계정은 언제나 같은 이음 user_id로 수렴한다.**
순차 호출뿐 아니라 동시 호출에서도 지켜져야 하므로 경합 케이스를 따로 둔다.
"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.channel import Channel
from app.models.platform_identity import PlatformIdentity, SlackChannel, SlackWorkspace
from app.models.server import Server, ServerMember
from app.models.user import User
from app.slack import mirror

TEAM = "T0TEST"
CHANNEL = "C0TEST"
USER = "U0TEST"


@pytest_asyncio.fixture
async def session_maker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_maker):
    async with session_maker() as session:
        yield session


# ── 신원 매핑 ────────────────────────────────────────────────────────


async def test_same_slack_user_maps_to_same_ieum_user(db):
    """핵심 케이스 — 같은 (team, user)로 두 번 부르면 같은 user_id가 나온다."""
    first = await mirror.ensure_identity(
        db, team_id=TEAM, slack_user_id=USER, display_name="민수"
    )
    await db.commit()
    second = await mirror.ensure_identity(
        db, team_id=TEAM, slack_user_id=USER, display_name="민수"
    )
    await db.commit()

    assert first.user_id == second.user_id
    assert await db.scalar(select(func.count()).select_from(PlatformIdentity)) == 1
    assert await db.scalar(select(func.count()).select_from(User)) == 1


async def test_same_user_id_in_different_workspace_is_a_different_account(db):
    """워크스페이스가 다르면 슬랙 user_id가 같아도 남남이다."""
    a = await mirror.ensure_identity(db, team_id="T0AAA", slack_user_id=USER, display_name="민수")
    b = await mirror.ensure_identity(db, team_id="T0BBB", slack_user_id=USER, display_name="민수")
    await db.commit()

    assert a.user_id != b.user_id


async def test_guest_user_has_no_credentials(db):
    """게스트는 이메일·비밀번호가 없다 — 슬랙에서 가져오는 건 표시 이름뿐이다."""
    identity = await mirror.ensure_identity(
        db, team_id=TEAM, slack_user_id=USER, display_name="민수"
    )
    await db.commit()

    guest = await db.get(User, identity.user_id)
    assert guest.is_guest is True
    assert guest.email is None
    assert guest.password_hash is None
    assert guest.display_name == "민수"


async def test_renaming_in_slack_does_not_overwrite_ieum_profile(db):
    """슬랙에서 이름을 바꿔도 웹에서 정한 이음 표시 이름은 지키다."""
    identity = await mirror.ensure_identity(
        db, team_id=TEAM, slack_user_id=USER, display_name="민수"
    )
    user = await db.get(User, identity.user_id)
    user.display_name = "내가 정한 이름"
    await db.commit()

    again = await mirror.ensure_identity(
        db, team_id=TEAM, slack_user_id=USER, display_name="민수(퇴사예정)"
    )
    await db.commit()

    assert again.display_name == "민수(퇴사예정)"  # 플랫폼 쪽 이름은 따라간다
    refreshed = await db.get(User, identity.user_id)
    assert refreshed.display_name == "내가 정한 이름"  # 이음 프로필은 그대로


async def test_concurrent_first_touch_converges_to_one_account(session_maker):
    """버튼을 동시에 두 번 눌러도 계정은 하나여야 한다.

    "조회 후 없으면 생성" 분기는 두 요청이 동시에 통과할 수 있다. UNIQUE 제약과
    SAVEPOINT 롤백이 실제로 이를 수습하는지 별도 세션 두 개로 확인한다.
    """

    async def touch():
        async with session_maker() as session:
            identity = await mirror.ensure_identity(
                session, team_id=TEAM, slack_user_id=USER, display_name="민수"
            )
            await session.commit()
            return identity.user_id

    first, second = await asyncio.gather(touch(), touch())
    assert first == second

    async with session_maker() as check:
        assert await check.scalar(select(func.count()).select_from(PlatformIdentity)) == 1
        # 경합에서 진 쪽이 만든 게스트가 고아로 남아 있으면 안 된다.
        assert await check.scalar(select(func.count()).select_from(User)) == 1


# ── 워크스페이스·채널·멤버십 ─────────────────────────────────────────


async def test_ensure_context_creates_full_mapping(db):
    user, channel = await mirror.ensure_context(
        db,
        team_id=TEAM,
        team_name="우리 팀",
        slack_channel_id=CHANNEL,
        channel_name="일반",
        slack_user_id=USER,
        display_name="민수",
    )
    await db.commit()

    workspace = await db.scalar(select(SlackWorkspace).where(SlackWorkspace.team_id == TEAM))
    server = await db.get(Server, workspace.server_id)
    assert server.name == "우리 팀"
    assert channel.server_id == server.id
    assert channel.name == "일반"

    # 멤버십이 없으면 웹에서 채널이 안 보인다.
    member = await db.scalar(
        select(ServerMember).where(
            ServerMember.server_id == server.id, ServerMember.user_id == user.id
        )
    )
    assert member is not None


async def test_ensure_context_is_idempotent(db):
    args = dict(
        team_id=TEAM,
        team_name="우리 팀",
        slack_channel_id=CHANNEL,
        channel_name="일반",
        slack_user_id=USER,
        display_name="민수",
    )
    user1, channel1 = await mirror.ensure_context(db, **args)
    await db.commit()
    user2, channel2 = await mirror.ensure_context(db, **args)
    await db.commit()

    assert user1.id == user2.id
    assert channel1.id == channel2.id
    assert await db.scalar(select(func.count()).select_from(Server)) == 1
    assert await db.scalar(select(func.count()).select_from(Channel)) == 1
    assert await db.scalar(select(func.count()).select_from(ServerMember)) == 1
    assert await db.scalar(select(func.count()).select_from(SlackChannel)) == 1


async def test_workspace_server_has_no_stray_default_channel(db):
    """슬랙 채널이 1:1로 미러링되므로, 대응 없는 기본 채널을 만들면 안 된다."""
    await mirror.ensure_context(
        db,
        team_id=TEAM,
        team_name="우리 팀",
        slack_channel_id=CHANNEL,
        channel_name="공지",
        slack_user_id=USER,
        display_name="민수",
    )
    await db.commit()

    names = (await db.scalars(select(Channel.name))).all()
    assert names == ["공지"]


async def test_two_slack_channels_share_one_server(db):
    common = dict(team_id=TEAM, team_name="우리 팀", slack_user_id=USER, display_name="민수")
    _, first = await mirror.ensure_context(
        db, slack_channel_id="C0AAA", channel_name="공지", **common
    )
    _, second = await mirror.ensure_context(
        db, slack_channel_id="C0BBB", channel_name="잡담", **common
    )
    await db.commit()

    assert first.id != second.id
    assert first.server_id == second.server_id
    assert await db.scalar(select(func.count()).select_from(Server)) == 1


# ── 로그인 가드 (기존 인증 흐름 회귀) ────────────────────────────────


async def test_guest_cannot_log_in(client, db_engine):
    """게스트 계정으로는 비밀번호 로그인이 안 된다.

    password_hash가 NULL이라 가드가 없으면 argon2가 InvalidHash를 던져 500이 난다.
    """
    session_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        # 게스트에 이메일을 억지로 채워, 조회는 되지만 로그인은 막히는 상황을 만든다.
        guest = User(display_name="민수", is_guest=True, email="guest@slack.test")
        session.add(guest)
        await session.commit()

    res = await client.post(
        "/auth/login", json={"email": "guest@slack.test", "password": "whatever"}
    )
    assert res.status_code == 401


async def test_normal_signup_and_login_still_work(client):
    """P1의 스키마 변경이 기존 정식 계정 흐름을 깨지 않았는지 확인한다."""
    signup = await client.post(
        "/auth/signup",
        json={"email": "real@example.com", "password": "pw12345678", "display_name": "정식"},
    )
    assert signup.status_code == 200

    login = await client.post(
        "/auth/login", json={"email": "real@example.com", "password": "pw12345678"}
    )
    assert login.status_code == 200

    me = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "real@example.com"
    assert me.json()["is_guest"] is False


@pytest.mark.parametrize("password", ["", "wrong-password"])
async def test_wrong_password_still_401(client, password):
    await client.post(
        "/auth/signup",
        json={"email": "real@example.com", "password": "pw12345678", "display_name": "정식"},
    )
    res = await client.post("/auth/login", json={"email": "real@example.com", "password": password})
    assert res.status_code in (401, 422)
