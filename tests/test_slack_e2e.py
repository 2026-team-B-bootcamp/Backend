"""슬랙 E2E — 실제 HTTP 요청이 슬랙 서명 검증을 통과해 핸들러를 돌고,
봇이 슬랙으로 **무엇을 내보내는지**까지 확인한다.

기존 test_slack.py는 엔드포인트 왕복을 5개만 본다(핑·도움말·서명·challenge·재전송).
그 테스트들은 `ack()` 응답 본문만 검사하는데, 이 봇이 실제로 하는 일의 대부분은
ack가 아니라 **바깥으로 나가는 호출**이다:

  · `respond()`        → response_url 로 POST      (버튼 클릭 결과 = 입장 링크)
  · `client.views_open` → Web API 호출              (태그 모달)
  · `say()`             → chat.postMessage          (멘션 응답)

그래서 여기서는 나가는 두 경로를 모두 가로채 기록하고(`SlackHarness`), 요청 →
핸들러 → DB → 바깥 호출까지 한 줄로 이어서 본다. 슬랙 API는 때리지 않지만
그 앞단(서명·Bolt 디스패치·핸들러·DB·토큰 발급)은 전부 진짜다.

가장 중요한 것은 §5의 핸드오프다: 슬랙 버튼 한 번으로 받은 링크의 토큰이
**실제 웹 API에 그대로 먹히는지**. 여기가 깨지면 시연이 그 자리에서 멈춘다.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from slack_sdk.webhook import WebhookResponse
from slack_sdk.webhook.async_client import AsyncWebhookClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.services.ai.base import IcebreakerProvider
from app.slack import blocks
from app.slack.features import FEATURES, GAMES, TOOLS

SIGNING_SECRET = "test-slack-signing-secret"
TEAM = "T0TEST"
CHANNEL = "C0TEST"
USER = "U0TEST"
OTHER = "U0OTHER"
RESPONSE_URL = "https://hooks.slack.test/actions/T0TEST/1/abc"


# ── 하네스 ───────────────────────────────────────────────────────────


class _StubIcebreakers(IcebreakerProvider):
    """질문 문구를 고정한다 — .env에 GEMINI_API_KEY가 있으면 실제 LLM을 때리기 때문."""

    cacheable = False

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def generate_templates(
        self, tags: list[str], count: int, avoid: list[str] | None = None
    ) -> list[str]:
        self.calls.append(list(tags))
        return [f"{{이름}}님, '{t}' 이야기 좀 들려주세요!" for t in tags][:count]


class SlackHarness:
    """슬랙 요청을 만들어 쏘고, 봇이 내보낸 것을 모아두는 도구."""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        # 슬랙 Web API 호출 (views.open, chat.postMessage, chat.postEphemeral, users.info …)
        self.api_calls: list[tuple[str, dict]] = []
        # respond() 가 response_url 로 POST 한 것들
        self.responses: list[tuple[str, dict]] = []
        self.icebreakers = _StubIcebreakers()

    # -- 조회 도우미 --

    def calls(self, method: str) -> list[dict]:
        return [args for name, args in self.api_calls if name == method]

    def last_response(self) -> dict:
        assert self.responses, "respond()가 한 번도 불리지 않았다"
        return self.responses[-1][1]

    @staticmethod
    def texts(body: dict) -> str:
        """블록 안 모든 텍스트를 한 덩어리로 — 문구 표현에 안 묶이고 내용만 본다."""
        return json.dumps(body, ensure_ascii=False)

    # -- 요청 만들기 --

    @staticmethod
    def _sign(body: str, timestamp: str) -> str:
        base = f"v0:{timestamp}:{body}".encode()
        return "v0=" + hmac.new(SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()

    async def _post(
        self,
        path: str,
        body: str,
        content_type: str,
        *,
        timestamp: str | None = None,
        signature: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        ts = timestamp or str(int(time.time()))
        headers = {
            "Content-Type": content_type,
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": signature or self._sign(body, ts),
        }
        headers.update(extra_headers or {})
        return await self.client.post(path, content=body, headers=headers)

    async def command(self, text: str, *, user_id: str = USER, **kw):
        """`/ieum <text>` 슬래시 커맨드."""
        body = urlencode(
            {
                "token": "verification-token",
                "team_id": TEAM,
                "team_domain": "우리팀",
                "channel_id": CHANNEL,
                "channel_name": "일반",
                "user_id": user_id,
                "user_name": "handle",
                "command": "/ieum",
                "text": text,
                "response_url": RESPONSE_URL,
                "trigger_id": "1.2.trigger",
            }
        )
        return await self._post(
            "/slack/commands", body, "application/x-www-form-urlencoded", **kw
        )

    async def interact(self, payload: dict, **kw):
        """버튼 클릭·선택기·모달 제출 — 슬랙은 form의 payload 필드에 JSON을 넣어 보낸다."""
        body = urlencode({"payload": json.dumps(payload, ensure_ascii=False)})
        return await self._post(
            "/slack/interactions", body, "application/x-www-form-urlencoded", **kw
        )

    async def event(self, payload: dict, **kw):
        body = json.dumps(payload, ensure_ascii=False)
        return await self._post("/slack/events", body, "application/json", **kw)


def block_action(
    action: dict, *, user_id: str = USER, channel_id: str = CHANNEL
) -> dict:
    """슬랙이 버튼/선택기 클릭 때 보내는 payload."""
    return {
        "type": "block_actions",
        "team": {"id": TEAM, "domain": "우리팀"},
        "user": {"id": user_id, "username": "handle", "name": "handle"},
        "channel": {"id": channel_id, "name": "일반"},
        "response_url": RESPONSE_URL,
        "trigger_id": "1.2.trigger",
        "container": {"type": "message", "channel_id": channel_id},
        "actions": [{"block_id": "b1", "action_ts": "1.0", **action}],
    }


def join_click(feature_key: str, *, action_id: str = blocks.JOIN_ACTION_ID, **kw) -> dict:
    return block_action(
        {"type": "button", "action_id": action_id, "value": feature_key}, **kw
    )


def view_submission(tags: tuple[str, str, str], *, user_id: str = USER,
                    channel_id: str = CHANNEL) -> dict:
    values = {
        field: {"value": {"type": "plain_text_input", "value": value}}
        for (field, _, _), value in zip(blocks.TAG_FIELDS, tags, strict=True)
    }
    return {
        "type": "view_submission",
        "team": {"id": TEAM, "domain": "우리팀"},
        "user": {"id": user_id, "username": "handle"},
        "view": {
            "id": "V0TEST",
            "type": "modal",
            "callback_id": blocks.TAG_MODAL_CALLBACK_ID,
            "private_metadata": channel_id,
            "hash": "1.abc",
            "state": {"values": values},
            "title": {"type": "plain_text", "text": "관심사 태그"},
            "blocks": [],
        },
    }


def mention_event(*, event_id: str = "Ev0TEST", ts: str = "1700000000.1",
                  thread_ts: str | None = None) -> dict:
    event: dict = {
        "type": "app_mention",
        "user": USER,
        "text": "<@U0BOT> 안녕",
        "ts": ts,
        "channel": CHANNEL,
    }
    if thread_ts:
        event["thread_ts"] = thread_ts
    return {
        "type": "event_callback",
        "event_id": event_id,
        "event_time": 1700000000,
        "team_id": TEAM,
        "api_app_id": "A0TEST",
        "event": event,
    }


@pytest_asyncio.fixture
async def slack(db_engine, monkeypatch):
    """슬랙이 켜진 앱 + 나가는 호출 전부를 가로챈 하네스."""
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test-token")
    monkeypatch.setattr(settings, "slack_signing_secret", SIGNING_SECRET)
    monkeypatch.setattr(settings, "public_web_url", "https://web.test")

    # 핸들러가 직접 여는 세션을 테스트 DB로 돌린다 (라우터가 아니라 Depends를 못 쓴다).
    monkeypatch.setattr(
        "app.db.base.async_session_maker",
        async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False),
    )

    harness_box: dict[str, SlackHarness] = {}

    async def fake_api_call(self, api_method, *, http_verb="POST", files=None,
                            data=None, params=None, json=None, headers=None, auth=None):
        # slack_sdk는 메서드마다 json/params/data 중 하나에 인자를 싣는다 — 다 합쳐 기록한다.
        args = {**(json or {}), **(params or {}), **(data or {})}
        payload: dict = {"ok": True}

        if api_method == "auth.test":
            # Bolt가 기동 때 토큰을 검증한다. 실제 슬랙을 때리지 않게 성공으로 갈음.
            payload = {
                "ok": True,
                "url": "https://test.slack.com/",
                "team": "테스트 워크스페이스",
                "user": "Deverapo",
                "team_id": TEAM,
                "user_id": "U0BOT",
                "bot_id": "B0BOT",
            }
        else:
            harness_box["h"].api_calls.append((api_method, args))
            if api_method == "users.info":
                # 표시 이름은 슬랙 핸들과 다르게 준다 — 어느 쪽을 쓰는지 구분되게.
                payload = {
                    "ok": True,
                    "user": {"profile": {"display_name": f"이름:{args.get('user')}"}},
                }

        return AsyncSlackResponse(
            client=self,
            http_verb=http_verb,
            api_url=f"https://slack.com/api/{api_method}",
            req_args={},
            data=payload,
            headers={},
            status_code=200,
        )

    async def fake_send_dict(self, body, headers=None):
        harness_box["h"].responses.append((self.url, body))
        return WebhookResponse(url=self.url, status_code=200, body="ok", headers={})

    monkeypatch.setattr(AsyncWebClient, "api_call", fake_api_call)
    monkeypatch.setattr(AsyncWebhookClient, "send_dict", fake_send_dict)

    # 설정을 바꾼 뒤에 빌드해야 Bolt가 테스트용 시크릿을 집는다.
    from app.slack.router import build_router

    test_app = FastAPI()
    test_app.include_router(build_router())

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        harness = SlackHarness(http)
        harness_box["h"] = harness
        # AI는 고정 문구 stub으로 (실제 Gemini 호출·과금 방지)
        monkeypatch.setattr("app.services.ai.provider._provider", harness.icebreakers)
        yield harness


# ══ §1. 슬래시 커맨드 → ack 본문 ═════════════════════════════════════


async def test_catalog_lists_every_feature_as_a_button(slack):
    """`/ieum 목록`은 카탈로그의 모든 기능을 버튼으로 내놔야 한다.

    기능을 추가하고 카탈로그에 안 넣으면 슬랙에서 영영 안 보인다.
    """
    res = await slack.command("목록")
    assert res.status_code == 200

    body = res.json()
    elements = [e for b in body["blocks"] if b["type"] == "actions" for e in b["elements"]]
    assert {e["value"] for e in elements} == {f.key for f in FEATURES}

    # 한 메시지 안에서 action_id가 겹치면 슬랙이 거부한다.
    action_ids = [e["action_id"] for e in elements]
    assert len(action_ids) == len(set(action_ids))
    # 게임과 도구가 각각의 actions 블록으로 나뉘어 있어야 읽기 쉽다.
    action_blocks = [b for b in body["blocks"] if b["type"] == "actions"]
    assert [len(b["elements"]) for b in action_blocks] == [len(GAMES), len(TOOLS)]


@pytest.mark.parametrize(
    ("text", "key"),
    [("게임 빙고", "bingo"), ("빙고", "bingo"), ("같이보기", "watch"), ("열기 그림판", "draw")],
)
async def test_opening_a_feature_posts_a_public_invite(slack, text, key):
    """기능을 열면 채널 전체가 보는 초대 메시지가 뜨고, 링크는 들어있지 않아야 한다."""
    res = await slack.command(text)
    body = res.json()

    assert body["response_type"] == "in_channel", "초대는 채널 전체에 보여야 참여가 붙는다"
    buttons = [e for b in body["blocks"] if b["type"] == "actions" for e in b["elements"]]
    assert [e["value"] for e in buttons] == [key]
    assert buttons[0]["action_id"] == blocks.JOIN_ACTION_ID
    # 공개 메시지에 링크가 섞이면 채널의 아무나 그 사람 행세를 할 수 있다.
    assert "http" not in slack.texts(body).replace(RESPONSE_URL, "")
    assert "url" not in buttons[0]


async def test_unknown_command_is_private_and_shows_help(slack):
    """모르는 명령을 채널 전체에 뿌리면 시끄럽다 — 본인에게만, 도움말과 함께.

    텍스트만 담은 ack는 평문으로 나가고, 슬랙은 response_type이 없으면 ephemeral로
    취급한다. 그래서 "in_channel이 아님"을 본문에 그 문자열이 없는 것으로 확인한다.
    """
    res = await slack.command("아무말대잔치")

    assert "in_channel" not in res.text
    assert "아무말대잔치" in res.text
    assert "/ieum 목록" in res.text


async def test_tag_edit_opens_a_modal_carrying_the_channel(slack):
    """태그등록은 모달이다. 채널을 private_metadata로 실어야 어디에 저장할지 알 수 있다."""
    res = await slack.command("태그등록")
    assert res.status_code == 200

    opened = slack.calls("views.open")
    assert len(opened) == 1, "모달이 열리지 않았다"
    view = opened[0]["view"]
    assert view["callback_id"] == blocks.TAG_MODAL_CALLBACK_ID
    assert view["private_metadata"] == CHANNEL
    assert opened[0]["trigger_id"] == "1.2.trigger"
    # 태그 칸은 전부 선택 입력이어야 한다 — 하나만 적어도 매칭은 된다.
    inputs = [b for b in view["blocks"] if b["type"] == "input"]
    assert len(inputs) == len(blocks.TAG_FIELDS)
    assert all(b["optional"] for b in inputs)


@pytest.mark.parametrize(
    ("text", "action_id"),
    [
        ("태그", blocks.PICK_TAGS_ACTION_ID),
        ("관심사", blocks.PICK_TAGS_ACTION_ID),
        ("말걸어줘", blocks.PICK_ICEBREAKER_ACTION_ID),
        ("질문", blocks.PICK_ICEBREAKER_ACTION_ID),
    ],
)
async def test_person_commands_offer_the_native_user_picker(slack, text, action_id):
    """이름을 타이핑시키면 동명이인·개명에 깨진다. 슬랙 기본 선택기(ID를 준다)를 써야 한다."""
    res = await slack.command(text)
    accessory = res.json()["blocks"][0]["accessory"]

    assert accessory["type"] == "users_select"
    assert accessory["action_id"] == action_id


# ══ §2. 버튼 클릭 → 개인 입장 링크 ═══════════════════════════════════


async def test_join_button_sends_a_private_entry_link(slack):
    """참여 버튼을 누른 사람에게만 개인 링크가 간다."""
    res = await slack.interact(join_click("bingo"))
    assert res.status_code == 200

    posted = slack.last_response()
    # replace_original=False — 채널의 초대 메시지는 살려둬야 다음 사람도 참여한다.
    assert posted.get("replace_original") is False

    buttons = [e for b in posted["blocks"] if b["type"] == "actions" for e in b["elements"]]
    link = buttons[0]["url"]
    parsed = urlparse(link)
    assert parsed.path.endswith("/play/bingo")
    assert parse_qs(parsed.query)["t"], "링크에 로그인 토큰이 실려야 한다"


async def test_catalog_button_uses_the_same_path(slack):
    """`/ieum 목록`의 버튼은 action_id가 `ieum_join_<key>`다 — 정규식 핸들러가 받아야 한다."""
    res = await slack.interact(
        join_click("wordchain", action_id=f"{blocks.JOIN_ACTION_ID}_wordchain")
    )
    assert res.status_code == 200

    buttons = [
        e for b in slack.last_response()["blocks"] if b["type"] == "actions"
        for e in b["elements"]
    ]
    assert urlparse(buttons[0]["url"]).path.endswith("/play/wordchain")


async def test_unknown_button_value_fails_softly(slack):
    """없어진 기능의 옛 버튼을 눌러도 500이 아니라 안내가 나가야 한다."""
    res = await slack.interact(join_click("사라진기능"))
    assert res.status_code == 200

    posted = slack.last_response()
    assert "/ieum 목록" in posted["text"]
    assert "blocks" not in posted or not posted.get("blocks")


async def test_two_people_clicking_get_different_links(slack):
    """링크는 개인 것이다 — 두 사람이 같은 토큰을 받으면 신원이 섞인다."""
    await slack.interact(join_click("bingo", user_id=USER))
    await slack.interact(join_click("bingo", user_id=OTHER))

    def token_of(body: dict) -> str:
        url = [e for b in body["blocks"] if b["type"] == "actions"
               for e in b["elements"]][0]["url"]
        return parse_qs(urlparse(url).query)["t"][0]

    first, second = (token_of(body) for _, body in slack.responses)
    assert first != second


async def test_open_link_button_is_acked(slack):
    """url 버튼도 인터랙션을 쏜다. 안 받으면 Bolt가 경고를 남기고 슬랙에 오류가 뜬다."""
    res = await slack.interact(
        block_action({"type": "button", "action_id": blocks.OPEN_LINK_ACTION_ID,
                      "url": "https://web.test/play/bingo"})
    )
    assert res.status_code == 200


# ══ §3. 태그 모달 · 사람 고르기 · AI ════════════════════════════════


async def test_tag_modal_submission_saves_and_confirms(slack, db_engine):
    """모달 제출 → DB 저장 → 본인에게만 보이는 확인 메시지."""
    res = await slack.interact(view_submission(("롤", "라멘", "클라이밍")))
    assert res.status_code == 200

    # 저장된 값을 DB에서 직접 확인한다.
    from app.services import tag_service
    from app.slack import mirror

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        user, channel = await mirror.ensure_context(
            db, team_id=TEAM, team_name="우리팀", slack_channel_id=CHANNEL,
            channel_name="일반", slack_user_id=USER, display_name="x",
        )
        tag = await tag_service.get_user_tags(db, channel.server_id, user.id)
        assert list(tag_service.tag_values(tag)) == ["롤", "라멘", "클라이밍"]

    # 확인은 채널에 뿌리지 않고 본인에게만.
    ephemeral = slack.calls("chat.postEphemeral")
    assert len(ephemeral) == 1
    assert ephemeral[0]["user"] == USER
    assert ephemeral[0]["channel"] == CHANNEL
    assert "롤" in ephemeral[0]["text"]


async def test_clearing_all_tags_says_so(slack):
    """세 칸을 다 비워 제출하면 '지웠다'고 알려야 한다 — 저장 실패로 오해하지 않게."""
    await slack.interact(view_submission(("롤", "라멘", "클라이밍")))
    slack.api_calls.clear()

    await slack.interact(view_submission(("", "", "")))
    text = slack.calls("chat.postEphemeral")[0]["text"]
    assert "지웠" in text


async def test_picking_a_person_shows_their_tags(slack):
    """태그를 등록한 사람을 고르면 그 태그가 카드로 나와야 한다."""
    await slack.interact(view_submission(("롤", "라멘", ""), user_id=OTHER))

    res = await slack.interact(
        block_action(
            {"type": "users_select", "action_id": blocks.PICK_TAGS_ACTION_ID,
             "selected_user": OTHER},
            user_id=USER,
        )
    )
    assert res.status_code == 200

    posted = slack.last_response()
    body = slack.texts(posted)
    assert "롤" in body and "라멘" in body
    # 선택기를 카드로 덮어써야 목록이 지저분하게 쌓이지 않는다.
    assert posted.get("replace_original") is True


async def test_picking_someone_with_no_tags_explains(slack):
    res = await slack.interact(
        block_action(
            {"type": "users_select", "action_id": blocks.PICK_TAGS_ACTION_ID,
             "selected_user": OTHER},
        )
    )
    assert res.status_code == 200
    assert "등록하지 않" in slack.texts(slack.last_response())


async def test_icebreaker_builds_questions_from_tags(slack):
    """AI 질문은 그 사람의 실제 태그를 재료로 만들어져야 한다."""
    await slack.interact(view_submission(("등산", "", ""), user_id=OTHER))

    res = await slack.interact(
        block_action(
            {"type": "users_select", "action_id": blocks.PICK_ICEBREAKER_ACTION_ID,
             "selected_user": OTHER},
            user_id=USER,
        )
    )
    assert res.status_code == 200

    assert slack.icebreakers.calls == [["등산"]], "대상의 태그가 AI에 전달되지 않았다"
    body = slack.texts(slack.last_response())
    assert "등산" in body
    # 템플릿의 {이름} 자리가 실제 이름으로 치환돼야 한다.
    assert "{이름}" not in body


async def test_icebreaker_without_tags_skips_the_ai_call(slack):
    """태그가 없으면 AI를 부를 이유가 없다 — 비용도 지연도 낭비다."""
    res = await slack.interact(
        block_action(
            {"type": "users_select", "action_id": blocks.PICK_ICEBREAKER_ACTION_ID,
             "selected_user": OTHER},
        )
    )
    assert res.status_code == 200
    assert slack.icebreakers.calls == []
    assert "관심사가 없어서" in slack.texts(slack.last_response())


# ══ §4. 이벤트 · 중복 · 보안 ═════════════════════════════════════════


async def test_mention_replies_in_a_thread(slack):
    """채널 타임라인을 봇 응답으로 어지럽히지 않도록 스레드로 답한다."""
    res = await slack.event(mention_event(ts="1700000000.5"))
    assert res.status_code == 200

    posted = slack.calls("chat.postMessage")
    assert len(posted) == 1
    assert posted[0]["thread_ts"] == "1700000000.5"
    assert "/ieum 목록" in posted[0]["text"]


async def test_mention_inside_a_thread_stays_in_that_thread(slack):
    """이미 스레드 안에서 부르면 새 스레드를 파지 말고 그 스레드에 답해야 한다."""
    await slack.event(mention_event(ts="1700000009.9", thread_ts="1700000000.1"))

    assert slack.calls("chat.postMessage")[0]["thread_ts"] == "1700000000.1"


async def test_the_same_event_is_only_handled_once(slack):
    """슬랙이 같은 이벤트를 또 보내도 봇이 두 번 떠들면 안 된다 (Redis SETNX)."""
    payload = mention_event(event_id="Ev0DUPLICATE")

    first = await slack.event(payload)
    second = await slack.event(payload)

    assert (first.status_code, second.status_code) == (200, 200)
    assert len(slack.calls("chat.postMessage")) == 1, "중복 이벤트가 두 번 처리됐다"


async def test_different_events_are_both_handled(slack):
    """중복 차단이 과해서 서로 다른 이벤트까지 먹으면 봇이 먹통이 된다."""
    await slack.event(mention_event(event_id="Ev0A"))
    await slack.event(mention_event(event_id="Ev0B"))

    assert len(slack.calls("chat.postMessage")) == 2


async def test_retry_delivery_is_dropped_before_handling(slack):
    """재전송 헤더가 붙으면 200만 주고 처리하지 않는다."""
    res = await slack.event(
        mention_event(event_id="Ev0RETRY"),
        extra_headers={"X-Slack-Retry-Num": "2", "X-Slack-Retry-Reason": "http_timeout"},
    )

    assert res.status_code == 200
    assert slack.calls("chat.postMessage") == []


@pytest.mark.parametrize("path", ["/slack/commands", "/slack/interactions", "/slack/events"])
async def test_every_endpoint_rejects_a_bad_signature(slack, path):
    """세 경로 다 서명을 봐야 한다 — 하나라도 뚫리면 누구나 봇을 조종한다."""
    body = urlencode({"text": "핑", "command": "/ieum", "user_id": USER})
    res = await slack._post(
        path, body, "application/x-www-form-urlencoded", signature="v0=deadbeef"
    )
    assert res.status_code == 401


async def test_missing_signature_headers_are_rejected(slack):
    res = await slack.client.post(
        "/slack/commands",
        content=urlencode({"text": "핑", "command": "/ieum"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 401


async def test_stale_timestamp_is_rejected(slack):
    """오래된 요청은 거부해야 한다 — 가로챈 요청을 나중에 재생하는 공격을 막는다."""
    old = str(int(time.time()) - 60 * 10)
    res = await slack.command("핑", timestamp=old)
    assert res.status_code == 401


async def test_tampered_body_is_rejected(slack):
    """서명은 맞지만 본문이 바뀐 요청 — 중간에서 명령을 갈아끼우는 경우."""
    ts = str(int(time.time()))
    original = urlencode({"command": "/ieum", "text": "핑", "user_id": USER})
    signature = SlackHarness._sign(original, ts)

    tampered = urlencode({"command": "/ieum", "text": "목록", "user_id": USER})
    res = await slack._post(
        "/slack/commands", tampered, "application/x-www-form-urlencoded",
        timestamp=ts, signature=signature,
    )
    assert res.status_code == 401


# ══ §5. 슬랙 → 웹 핸드오프 (발표의 핵심 경로) ═══════════════════════


async def test_slack_button_to_working_web_session(slack, client, db_engine):
    """버튼 한 번 → 받은 링크의 토큰이 실제 웹 API에 그대로 먹혀야 한다.

    이 봇의 존재 이유가 이 한 줄이다. 토큰이 형식만 맞고 인증에서 튕기면
    시연이 그 자리에서 멈춘다. 그래서 링크를 쪼개 실제 API에 넣어본다.
    """
    await slack.interact(join_click("bingo"))

    url = [
        e for b in slack.last_response()["blocks"] if b["type"] == "actions"
        for e in b["elements"]
    ][0]["url"]
    parsed = urlparse(url)
    token = parse_qs(parsed.query)["t"][0]
    # /servers/{sid}/channels/{cid}/play/bingo
    _, _, server_id, _, channel_id, _, feature = parsed.path.split("/")
    assert feature == "bingo"

    headers = {"Authorization": f"Bearer {token}"}

    # ① 그 토큰이 로그인된 사람으로 통해야 한다
    me = await client.get("/users/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["display_name"] == f"이름:{USER}", "슬랙 표시 이름이 계정에 붙지 않았다"

    # ② 링크가 가리키는 채널에 실제로 들어가 있어야 한다 (멤버십까지 만들어졌는지)
    channels = await client.get(f"/servers/{server_id}/channels", headers=headers)
    assert channels.status_code == 200, channels.text
    assert int(channel_id) in [c["id"] for c in channels.json()]

    # ③ 링크가 가리키는 그 게임을 실제로 시작할 수 있어야 한다
    started = await client.post(f"/channels/{channel_id}/bingo/join", headers=headers)
    assert started.status_code in (200, 201), started.text


async def test_two_slack_users_land_in_the_same_channel(slack, client):
    """같은 슬랙 채널에서 누른 두 사람은 같은 판에서 만나야 한다 — 아니면 같이 못 논다."""
    links = []
    for uid in (USER, OTHER):
        await slack.interact(join_click("omok", user_id=uid))
        url = [
            e for b in slack.last_response()["blocks"] if b["type"] == "actions"
            for e in b["elements"]
        ][0]["url"]
        links.append(urlparse(url))

    assert links[0].path == links[1].path, "같은 채널·같은 기능으로 가야 한다"

    # 서로 다른 계정이면서, 같은 채널의 같은 게임에 둘 다 참여할 수 있어야 한다.
    tokens = [parse_qs(p.query)["t"][0] for p in links]
    assert tokens[0] != tokens[1]

    channel_id = links[0].path.split("/")[4]
    ids = []
    for token in tokens:
        headers = {"Authorization": f"Bearer {token}"}
        me = await client.get("/users/me", headers=headers)
        assert me.status_code == 200, me.text
        ids.append(me.json()["id"])
        joined = await client.post(f"/channels/{channel_id}/omok/join", headers=headers)
        assert joined.status_code in (200, 201), joined.text

    assert ids[0] != ids[1], "두 슬랙 유저가 한 계정으로 합쳐졌다"


async def test_entry_link_never_carries_the_token_in_the_path(slack):
    """토큰이 경로에 들어가면 서버 접근 로그·리퍼러에 그대로 남는다."""
    await slack.interact(join_click("draw"))

    url = [
        e for b in slack.last_response()["blocks"] if b["type"] == "actions"
        for e in b["elements"]
    ][0]["url"]
    parsed = urlparse(url)
    assert parse_qs(parsed.query)["t"][0] not in parsed.path
