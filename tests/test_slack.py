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
from app.slack.handlers import HELP_TEXT, build_command_reply, parse_command

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


def test_build_command_reply():
    assert "퐁" in build_command_reply("핑")
    assert build_command_reply("") == HELP_TEXT
    # 모르는 명령은 원본을 되비추고 도움말을 함께 준다.
    unknown = build_command_reply("없는명령")
    assert "없는명령" in unknown
    assert HELP_TEXT in unknown


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
