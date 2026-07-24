"""슬랙 버튼 → 개인 입장 링크 (P2).

발표의 핵심 흐름이라 링크가 실제로 "그 사람으로 로그인되고, 그 채널의, 그 기능이
열린" 상태를 가리키는지 끝까지 확인한다. 링크의 토큰을 실제 API에 넣어보는
테스트까지 둔 이유다 — 토큰이 형식만 맞고 인증에 실패하면 데모가 그 자리에서 깨진다.
"""

from urllib.parse import parse_qs, urlparse

import jwt
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.security import decode_access_token
from app.slack import mirror
from app.slack.features import by_key, find
from app.slack.link_token import LINK_TOKEN_EXPIRE_MINUTES, build_entry_link

TEAM = "T0TEST"
CHANNEL = "C0TEST"
USER = "U0TEST"


@pytest_asyncio.fixture
async def db(db_engine):
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


async def _context(db, slack_user_id=USER, display_name="민수"):
    user, channel = await mirror.ensure_context(
        db,
        team_id=TEAM,
        team_name="우리 팀",
        slack_channel_id=CHANNEL,
        channel_name="일반",
        slack_user_id=slack_user_id,
        display_name=display_name,
    )
    await db.commit()
    return user, channel


async def test_link_points_at_the_right_channel_and_feature(db):
    user, channel = await _context(db)
    link = build_entry_link(user, channel, by_key("bingo"))

    parsed = urlparse(link)
    # 채팅방이 아니라 빙고만 있는 전용 화면으로 보낸다.
    assert parsed.path == f"/servers/{channel.server_id}/channels/{channel.id}/play/bingo"
    assert parsed.scheme in ("http", "https")


async def test_link_token_identifies_the_slack_user(db):
    """링크의 토큰은 그 슬랙 계정에 매핑된 이음 계정이어야 한다."""
    user, channel = await _context(db)
    link = build_entry_link(user, channel, by_key("watch"))

    token = parse_qs(urlparse(link).query)["t"][0]
    payload = decode_access_token(token)
    assert payload["sub"] == str(user.id)
    assert payload["ver"] == user.token_version


async def test_link_token_expires_in_15_minutes(db):
    """유출 대비 — 기본 24시간이 아니라 짧게 끊어야 한다."""
    user, channel = await _context(db)
    link = build_entry_link(user, channel, by_key("draw"))
    token = parse_qs(urlparse(link).query)["t"][0]

    payload = decode_access_token(token)
    lifetime_minutes = (payload["exp"] - payload["iat"]) / 60 if "iat" in payload else None
    if lifetime_minutes is None:
        # iat를 넣지 않으므로 발급 시각 기준으로 대략 확인한다.
        import time

        lifetime_minutes = (payload["exp"] - time.time()) / 60
    assert LINK_TOKEN_EXPIRE_MINUTES - 1 < lifetime_minutes <= LINK_TOKEN_EXPIRE_MINUTES
    # 기본 만료(24시간)를 그대로 쓰고 있지 않은지 못 박는다.
    assert lifetime_minutes < settings.jwt_expire_minutes


async def test_two_slack_users_get_different_links(db):
    """계정당 1슬롯 — 서로 다른 사람은 서로 다른 이음 계정을 가리켜야 한다."""
    a_user, channel = await _context(db, slack_user_id="U0AAA", display_name="A")
    b_user, _ = await _context(db, slack_user_id="U0BBB", display_name="B")

    a = parse_qs(urlparse(build_entry_link(a_user, channel, by_key("bingo"))).query)["t"][0]
    b = parse_qs(urlparse(build_entry_link(b_user, channel, by_key("bingo"))).query)["t"][0]
    assert decode_access_token(a)["sub"] != decode_access_token(b)["sub"]


async def test_same_user_clicking_twice_stays_one_account(db):
    """같은 사람이 두 번 눌러도 계정이 늘어나면 안 된다."""
    first, channel = await _context(db)
    second, _ = await _context(db)
    assert first.id == second.id

    a = parse_qs(urlparse(build_entry_link(first, channel, by_key("omok"))).query)["t"][0]
    b = parse_qs(urlparse(build_entry_link(second, channel, by_key("omok"))).query)["t"][0]
    assert decode_access_token(a)["sub"] == decode_access_token(b)["sub"]


@pytest.mark.parametrize(
    "word", ["빙고", "끝말잇기", "오목", "틱택토", "밸런스게임", "초성퀴즈", "같이보기", "그림판"]
)
async def test_every_feature_produces_a_usable_link(db, word):
    """웹에서 되는 것은 슬랙에서도 전부 열려야 한다."""
    user, channel = await _context(db)
    feature = find(word)
    assert feature is not None
    link = build_entry_link(user, channel, feature)
    assert urlparse(link).path.endswith(f"/play/{feature.page}")


async def test_link_token_actually_authenticates(client, db_engine):
    """형식만 맞는 게 아니라 실제 API 인증을 통과해야 한다.

    이게 깨지면 링크를 눌러도 웹이 로그인 화면으로 튕긴다 — 데모가 그 자리에서 끝난다.
    """
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        user, channel = await mirror.ensure_context(
            session,
            team_id=TEAM,
            team_name="우리 팀",
            slack_channel_id=CHANNEL,
            channel_name="일반",
            slack_user_id=USER,
            display_name="민수",
        )
        await session.commit()
        link = build_entry_link(user, channel, by_key("bingo"))
        expected_id, channel_id = user.id, channel.id

    token = parse_qs(urlparse(link).query)["t"][0]
    res = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["id"] == expected_id
    assert res.json()["is_guest"] is True

    # 그리고 그 채널에 실제로 들어갈 수 있어야 한다(멤버십이 없으면 403).
    res = await client.get(
        f"/channels/{channel_id}/games/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200


# ── 버튼 클릭 → 링크 발급 (핸들러 경로) ──────────────────────────────


@pytest_asyncio.fixture
async def slack_session(db_engine, monkeypatch):
    """핸들러가 여는 세션을 테스트 DB로 돌린다.

    `_issue_entry_link`는 라우터가 아니라 Depends(get_db)를 못 쓰고 직접 세션을
    연다. 이 픽스처가 없으면 테스트가 개발 DB에 써버린다.
    """
    monkeypatch.setattr(
        "app.db.base.async_session_maker",
        async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False),
    )


class _FakeSlackClient:
    """users.info만 흉내내는 최소 클라이언트."""

    def __init__(self, display_name: str | None = None, fail: bool = False):
        self._display_name = display_name
        self._fail = fail
        self.calls: list[str] = []

    async def users_info(self, user: str):
        self.calls.append(user)
        if self._fail:
            raise RuntimeError("slack down")
        return {"user": {"profile": {"display_name": self._display_name}}}


def _action_body(slack_user_id: str = USER, username: str = "handle") -> dict:
    """슬랙이 버튼 클릭 때 보내는 payload의 필요한 부분만."""
    return {
        "team": {"id": TEAM, "domain": "우리팀"},
        "channel": {"id": CHANNEL, "name": "일반"},
        "user": {"id": slack_user_id, "username": username},
        "actions": [{"value": "bingo"}],
    }


async def test_issue_entry_link_creates_everything(db_engine, slack_session):
    """버튼 한 번으로 계정·서버·채널·멤버십이 다 생기고 링크가 나와야 한다."""
    from app.slack.handlers import _issue_entry_link

    fake = _FakeSlackClient(display_name="박민수")
    link = await _issue_entry_link(fake, _action_body(), by_key("bingo"))

    assert urlparse(link).path.endswith("/play/bingo")
    query = parse_qs(urlparse(link).query)

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        from sqlalchemy import func, select

        from app.models.server import ServerMember
        from app.models.user import User

        # 슬랙 표시 이름이 이음 계정 이름으로 들어갔는지
        user_id = int(decode_access_token(query["t"][0])["sub"])
        user = await session.get(User, user_id)
        assert user.display_name == "박민수"
        assert user.is_guest is True
        # 멤버십이 없으면 웹에서 채널이 안 보인다
        assert await session.scalar(select(func.count()).select_from(ServerMember)) == 1


async def test_issue_entry_link_survives_users_info_failure(db_engine, slack_session):
    """이름 조회가 실패해도 입장은 되어야 한다 — 이름 하나로 데모가 막히면 안 된다."""
    from app.slack.handlers import _issue_entry_link

    fake = _FakeSlackClient(fail=True)
    link = await _issue_entry_link(fake, _action_body(username="handle"), by_key("bingo"))

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        from app.models.user import User

        user_id = int(decode_access_token(parse_qs(urlparse(link).query)["t"][0])["sub"])
        user = await session.get(User, user_id)
        # 페이로드의 핸들로 폴백한다
        assert user.display_name == "handle"


async def test_two_clickers_get_separate_accounts_same_channel(db_engine, slack_session):
    from app.slack.handlers import _issue_entry_link

    a = await _issue_entry_link(_FakeSlackClient("A"), _action_body("U0AAA"), by_key("bingo"))
    b = await _issue_entry_link(_FakeSlackClient("B"), _action_body("U0BBB"), by_key("omok"))

    a_q, b_q = parse_qs(urlparse(a).query), parse_qs(urlparse(b).query)
    assert decode_access_token(a_q["t"][0])["sub"] != decode_access_token(b_q["t"][0])["sub"]
    # 다른 사람이어도 같은 채널로 들어가야 한다 — 같이 놀아야 하므로
    channel_path = lambda u: urlparse(u).path.rsplit("/play/", 1)[0]  # noqa: E731
    assert channel_path(a) == channel_path(b)
    assert urlparse(a).path.endswith("/play/bingo")
    assert urlparse(b).path.endswith("/play/omok")


async def test_expired_link_is_rejected(client, db_engine, monkeypatch):
    """15분이 지난 링크로는 못 들어가야 한다."""
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        user, channel = await mirror.ensure_context(
            session,
            team_id=TEAM,
            team_name="우리 팀",
            slack_channel_id=CHANNEL,
            channel_name="일반",
            slack_user_id=USER,
            display_name="민수",
        )
        await session.commit()
        # 이미 만료된 토큰을 만든다.
        monkeypatch.setattr("app.slack.link_token.LINK_TOKEN_EXPIRE_MINUTES", -1)
        from app.core.security import create_access_token

        token = create_access_token(str(user.id), user.token_version, expire_minutes=-1)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)

    res = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# ── 전용 화면 경로 규칙 ──────────────────────────────────────────────


async def test_tags_link_goes_to_tag_page(db):
    """태그 등록도 슬랙에서 바로 열 수 있어야 한다."""
    user, channel = await _context(db)
    link = build_entry_link(user, channel, by_key("tags"))
    assert urlparse(link).path.endswith("/play/tags")


async def test_chat_link_has_no_play_segment(db):
    """채팅은 전용 화면이 없다 — 채널 화면 자체가 목적지다."""
    user, channel = await _context(db)
    link = build_entry_link(user, channel, by_key("chat"))
    path = urlparse(link).path
    assert path == f"/servers/{channel.server_id}/channels/{channel.id}"
    assert "/play/" not in path


async def test_token_never_leaks_into_the_path(db):
    """토큰은 쿼리에만 있어야 한다 — 경로에 섞이면 서버 접근 로그에 그대로 남는다."""
    user, channel = await _context(db)
    for feature_key in ("bingo", "watch", "tags", "chat"):
        link = build_entry_link(user, channel, by_key(feature_key))
        parsed = urlparse(link)
        assert "t=" not in parsed.path
        assert parse_qs(parsed.query)["t"]
