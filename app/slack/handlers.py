"""슬랙 이벤트·커맨드 핸들러.

핵심 원칙 두 가지:
① 커맨드 문자열 파싱은 순수 함수(`parse_command`)로 분리한다 — 슬랙 없이 테스트 가능하게.
② 슬랙은 3초 안에 200을 못 받으면 재전송하므로 핸들러 첫 줄은 항상 `ack()`다.

기능(게임·같이보기·그림판…)마다 핸들러를 만들지 않는다. 슬랙이 하는 일은 어느
경우든 "개인 링크를 발급해 웹의 해당 패널을 열어주는 것" 하나뿐이라, features.py의
카탈로그를 읽는 경로 하나로 전부 처리한다.
"""

import logging
import re
from dataclasses import dataclass

from slack_bolt.async_app import AsyncApp

from app.db import base as db_base
from app.slack import blocks, mirror
from app.slack.features import Feature, by_key, catalog_text, find
from app.slack.link_token import build_entry_link

logger = logging.getLogger(__name__)

# 서브커맨드 별칭 표. 한글이 기본이고 영문은 편의용 별칭이다.
_ALIASES: dict[str, str] = {
    "핑": "ping",
    "ping": "ping",
    "도움말": "help",
    "help": "help",
    "?": "help",
    "목록": "catalog",
    "메뉴": "catalog",
    "list": "catalog",
    # "게임 빙고"처럼 한 단계 더 들어가는 형태. 뒤 단어를 기능 이름으로 읽는다.
    "게임": "open",
    "열기": "open",
    "시작": "open",
}

HELP_TEXT = (
    "*이음* — 관심사 태그로 빠르게 친해지는 아이스브레이킹 봇\n\n"
    "• `/ieum 목록` — 열 수 있는 것 전부 버튼으로 보기\n"
    "• `/ieum 게임 빙고` — 채널에 참여 버튼을 띄운다\n"
    "• `/ieum 같이보기` — 바로 이름을 불러도 된다\n"
    "• `/ieum 핑` — 봇이 살아있는지 확인\n\n" + catalog_text()
)


@dataclass(frozen=True)
class ParsedCommand:
    """슬래시 커맨드 텍스트를 쪼갠 결과."""

    name: str  # 별칭이 풀린 정규 서브커맨드 이름. 알 수 없으면 "unknown"
    args: str  # 서브커맨드 뒤에 남은 나머지 문자열(앞뒤 공백 제거)
    raw: str  # 원본 텍스트(앞뒤 공백만 제거) — 알 수 없는 입력을 되비출 때 쓴다


def parse_command(text: str | None) -> ParsedCommand:
    """`/ieum <서브커맨드> <나머지>` 의 텍스트 부분을 파싱한다.

    슬랙은 텍스트가 없으면 빈 문자열을 보내고, 필드 자체가 없을 수도 있어
    None도 받는다. 둘 다 도움말로 취급한다.
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedCommand(name="help", args="", raw="")

    head, _, rest = raw.partition(" ")
    # 슬랙 모바일 등에서 전각 공백이 섞여 들어오는 경우가 있어 같이 정리한다.
    name = _ALIASES.get(head.strip("　").lower(), "unknown")
    return ParsedCommand(name=name, args=rest.strip(), raw=raw)


def resolve_feature(parsed: ParsedCommand) -> Feature | None:
    """파싱 결과에서 열려는 기능을 찾는다.

    `게임 빙고`(서브커맨드 + 인자)와 `빙고`(이름 직접) 둘 다 받는다. 후자는
    사람들이 실제로 그렇게 치기 때문이고, 별칭 표가 겹치지 않아 모호하지 않다.
    """
    if parsed.name == "open":
        return find(parsed.args) if parsed.args else None
    if parsed.name == "unknown":
        return find(parsed.raw)
    return None


def unknown_reply(parsed: ParsedCommand) -> str:
    if parsed.name == "open" and not parsed.args:
        return f"무엇을 열까요? 예: `/ieum 게임 빙고`\n\n{catalog_text()}"
    return f"모르는 명령이에요: `{parsed.raw}`\n\n{HELP_TEXT}"


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

    슬랙 신원을 이음 계정으로 미러링하고(없으면 게스트 생성), 그 계정으로
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


def register(app: AsyncApp) -> None:
    """Bolt 앱에 핸들러를 붙인다."""

    @app.command("/ieum")
    async def handle_ieum(ack, command) -> None:
        parsed = parse_command(command.get("text"))

        if parsed.name == "ping":
            await ack(text="퐁 🏓")
            return
        if parsed.name == "help":
            await ack(text=HELP_TEXT)
            return
        if parsed.name == "catalog":
            await ack(blocks=blocks.catalog_blocks(), text="열 수 있는 것들")
            return

        feature = resolve_feature(parsed)
        if feature is None:
            await ack(text=unknown_reply(parsed))
            return

        # 채널 전체에 보이는 초대 메시지. 링크는 없고 버튼만 있다.
        await ack(
            blocks=blocks.invite_blocks(feature, command.get("user_id", "")),
            text=f"{feature.label} 열림",
            response_type="in_channel",
        )

    # 초대 메시지는 `ieum_join` 하나지만, `/ieum 목록`은 버튼이 여러 개라
    # `ieum_join_<기능>`으로 갈린다 (한 메시지 안에서 action_id가 겹치면 안 된다).
    # 둘 다 같은 처리라 정규식 하나로 받는다.
    @app.action(re.compile(rf"^{blocks.JOIN_ACTION_ID}(_.+)?$"))
    async def handle_join(ack, body, client, respond) -> None:
        # 먼저 ack — 링크 발급(DB + users.info)이 3초를 넘길 여지를 없앤다.
        await ack()
        actions = body.get("actions") or [{}]
        feature = by_key(actions[0].get("value", ""))
        if feature is None:
            await respond(text="알 수 없는 항목이에요. `/ieum 목록`으로 다시 시도해주세요.")
            return

        try:
            link = await _issue_entry_link(client, body, feature)
        except Exception:
            logger.exception("입장 링크 발급 실패")
            await respond(text="입장 링크를 만들지 못했어요. 잠시 후 다시 눌러주세요.")
            return

        await respond(
            blocks=blocks.entry_link_blocks(feature, link),
            text=f"{feature.label} 입장 링크",
            replace_original=False,
        )

    @app.action(blocks.OPEN_LINK_ACTION_ID)
    async def handle_open_link(ack) -> None:
        # url 버튼은 슬랙이 알아서 브라우저를 여니 우리가 할 일은 없다. ack만 한다.
        await ack()

    @app.event("app_mention")
    async def handle_app_mention(event, say) -> None:
        # 스레드 안에서 멘션되면 같은 스레드로, 아니면 그 메시지에 스레드를 열어 답한다
        # — 채널 타임라인을 봇 응답으로 어지럽히지 않기 위해서다.
        await say(
            text=f"안녕하세요! `/ieum 목록`으로 시작해보세요.\n\n{catalog_text()}",
            thread_ts=event.get("thread_ts") or event.get("ts"),
        )
