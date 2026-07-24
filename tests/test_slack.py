"""슬랙 어댑터 테스트 (P0).

두 층으로 나눠 본다:
① 커맨드 파싱/응답 생성 — 순수 함수라 슬랙 없이 그대로 검증한다.
② 엔드포인트 왕복 — 실제 슬랙 서명을 만들어 붙여, 서명 검증부터 `퐁`까지 확인한다.
   서명이 틀리면 401이 나야 한다는 것도 같이 본다.
"""

import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from app.core.config import settings
from app.slack import features
from app.slack.handlers import HELP_TEXT, parse_command, resolve_feature, unknown_reply

SIGNING_SECRET = "test-slack-signing-secret"


# ── ① 순수 함수 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("핑", "ping"),
        ("  핑  ", "ping"),
        ("ping", "ping"),
        ("PING", "ping"),
        ("도움말", "help"),
        ("", "help"),
        ("   ", "help"),
        (None, "help"),  # 슬랙이 text 필드를 아예 안 보내는 경우
        ("없는명령", "unknown"),
    ],
)
def test_parse_command_name(text, expected):
    assert parse_command(text).name == expected


def test_parse_command_keeps_args():
    parsed = parse_command("태그 @민수 롤 좋아함")
    assert parsed.args == "@민수 롤 좋아함"
    assert parsed.raw == "태그 @민수 롤 좋아함"


def test_unknown_reply_echoes_input_with_help():
    reply = unknown_reply(parse_command("없는명령"))
    assert "없는명령" in reply
    assert HELP_TEXT in reply


# ── 기능 카탈로그 해석 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected_key"),
    [
        ("게임 빙고", "bingo"),
        ("게임 오목", "omok"),
        ("열기 그림판", "draw"),
        ("시작 같이보기", "watch"),
        # 서브커맨드 없이 이름만 쳐도 열린다 — 사람들이 실제로 이렇게 친다.
        ("빙고", "bingo"),
        ("끝말잇기", "wordchain"),
        ("유튜브", "watch"),
        ("그림판", "draw"),
        # 영문 별칭과 대소문자
        ("BINGO", "bingo"),
        ("게임 Omok", "omok"),
    ],
)
def test_resolve_feature(text, expected_key):
    feature = resolve_feature(parse_command(text))
    assert feature is not None, f"{text} 를 해석하지 못했다"
    assert feature.key == expected_key


@pytest.mark.parametrize(
    "text",
    [
        "핑",
        "도움말",
        "",
        "없는게임",
        "게임 없는게임",
        # 슬랙 안에서 끝나는 명령들은 웹으로 내보낼 기능이 아니다
        "태그",
        "태그등록",
        "말걸어줘",
    ],
)
def test_resolve_feature_returns_none(text):
    assert resolve_feature(parse_command(text)) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("태그등록", "tag_edit"),
        ("내태그", "tag_edit"),
        ("태그", "tag_view"),
        ("관심사", "tag_view"),
        ("말걸어줘", "icebreaker"),
        ("아이스브레이커", "icebreaker"),
    ],
)
def test_slack_native_commands(text, expected):
    """태그·AI는 슬랙 안에서 끝난다 — 웹 링크로 내보내지 않는다."""
    assert parse_command(text).name == expected


def test_catalog_only_has_shared_activities():
    """슬랙에서 웹으로 보내는 것은 '여럿이 같이 하는 것'뿐이다.

    채팅·멤버목록처럼 혼자 보는 화면까지 링크로 내보내면, 슬랙에서 이미 대화
    중인 사람을 굳이 브라우저로 쫓아내는 셈이 된다.
    """
    keys = {f.key for f in features.FEATURES}
    assert "chat" not in keys
    assert "members" not in keys
    assert {"watch", "draw"} <= keys
    # 모든 기능이 전용 화면을 갖는다 — 빈 page가 있으면 링크가 깨진다
    assert all(f.page for f in features.FEATURES)


def test_game_catalog_matches_web():
    """웹 게임 패널(GamePip)의 6종과 어긋나면 슬랙에서 못 여는 게임이 생긴다."""
    assert {f.key for f in features.GAMES} == {
        "bingo",
        "wordchain",
        "omok",
        "tictactoe",
        "balance",
        "chosung",
    }


def test_every_feature_is_reachable_by_its_own_label():
    """모든 기능은 라벨 그대로 쳐서 열 수 있어야 한다(도움말에 라벨을 노출하므로)."""
    for f in features.FEATURES:
        assert features.find(f.label) is f, f"{f.label} 로 찾을 수 없다"


def test_feature_keys_and_aliases_are_unique():
    keys = [f.key for f in features.FEATURES]
    assert len(keys) == len(set(keys))
    aliases = [a.lower() for f in features.FEATURES for a in f.aliases]
    assert len(aliases) == len(set(aliases)), "별칭이 겹치면 엉뚱한 기능이 열린다"


# ── ② 엔드포인트 왕복 ────────────────────────────────────────────────


def _sign(body: str, timestamp: str) -> str:
    """슬랙과 동일한 방식으로 v0 서명을 만든다."""
    basestring = f"v0:{timestamp}:{body}".encode()
    digest = hmac.new(SIGNING_SECRET.encode(), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


@pytest.fixture
def slack_app(monkeypatch):
    """슬랙 설정이 켜진 상태의 FastAPI 앱을 만든다."""
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test-token")
    monkeypatch.setattr(settings, "slack_signing_secret", SIGNING_SECRET)

    # Bolt는 요청을 처리하기 전에 토큰이 유효한지 auth.test로 확인한다(결과는 캐시).
    # 테스트에서 실제 슬랙 API를 때리지 않도록 성공 응답으로 갈음한다.
    async def fake_auth_test(self, **kwargs):
        return AsyncSlackResponse(
            client=self,
            http_verb="POST",
            api_url="https://slack.com/api/auth.test",
            req_args={},
            data={
                "ok": True,
                "url": "https://test.slack.com/",
                "team": "테스트 워크스페이스",
                "user": "이음",
                "team_id": "T0TEST",
                "user_id": "U0BOT",
                "bot_id": "B0BOT",
            },
            headers={},
            status_code=200,
        )

    monkeypatch.setattr(AsyncWebClient, "auth_test", fake_auth_test)

    # 설정을 바꾼 뒤에 빌드해야 Bolt가 테스트용 시크릿을 집는다.
    from app.slack.router import build_router

    test_app = FastAPI()
    test_app.include_router(build_router())
    return test_app


@pytest.fixture
async def slack_client(slack_app):
    transport = ASGITransport(app=slack_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _post_command(client, text: str, *, signed: bool = True):
    body = urlencode(
        {
            "token": "verification-token",
            "team_id": "T0TEST",
            "channel_id": "C0TEST",
            "user_id": "U0TEST",
            "command": "/ieum",
            "text": text,
            "response_url": "https://hooks.slack.test/commands/T0TEST/1",
            "trigger_id": "1.2.abc",
        }
    )
    timestamp = str(int(time.time()))
    signature = _sign(body, timestamp) if signed else "v0=deadbeef"
    return await client.post(
        "/slack/commands",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        },
    )


async def test_ping_returns_pong(slack_client):
    res = await _post_command(slack_client, "핑")
    assert res.status_code == 200
    assert "퐁" in res.text


async def test_help_is_default(slack_client):
    res = await _post_command(slack_client, "")
    assert res.status_code == 200
    assert "이음" in res.text


async def test_invalid_signature_is_rejected(slack_client):
    """서명이 틀리면 통과시키면 안 된다 — 누구나 봇을 조종할 수 있게 된다."""
    res = await _post_command(slack_client, "핑", signed=False)
    assert res.status_code == 401


async def test_url_verification_challenge(slack_client):
    """슬랙이 Request URL을 저장할 때 쏘는 challenge에 그대로 응답해야 한다."""
    import json

    body = json.dumps({"type": "url_verification", "challenge": "abc123xyz"})
    timestamp = str(int(time.time()))
    res = await slack_client.post(
        "/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": _sign(body, timestamp),
        },
    )
    assert res.status_code == 200
    assert "abc123xyz" in res.text


async def test_retry_is_skipped(slack_client):
    """재전송 헤더가 붙은 요청은 200만 주고 실제 처리는 하지 않는다."""
    import json

    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev0TEST",
            "team_id": "T0TEST",
            "event": {"type": "app_mention", "user": "U0TEST", "ts": "1.0", "channel": "C0TEST"},
        }
    )
    timestamp = str(int(time.time()))
    res = await slack_client.post(
        "/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": _sign(body, timestamp),
            "X-Slack-Retry-Num": "1",
            "X-Slack-Retry-Reason": "http_timeout",
        },
    )
    assert res.status_code == 200
