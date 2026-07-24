"""슬랙 이벤트·커맨드 핸들러.

핵심 원칙 두 가지:
① 커맨드 문자열 파싱은 순수 함수(`parse_command`)로 분리한다 — 슬랙 없이 테스트 가능하게.
② 슬랙은 3초 안에 200을 못 받으면 재전송하므로 핸들러 첫 줄은 항상 `ack()`다.
"""

from dataclasses import dataclass

from slack_bolt.async_app import AsyncApp

# 서브커맨드 별칭 표. 한글이 기본이고 영문은 편의용 별칭이다.
# 한글 슬래시 커맨드(`/이음`) 등록 가능 여부와 무관하게, 커맨드 뒤에 오는
# 서브커맨드는 그냥 텍스트라 한글이 항상 안전하다.
_ALIASES: dict[str, str] = {
    "핑": "ping",
    "ping": "ping",
    "도움말": "help",
    "help": "help",
    "?": "help",
}

HELP_TEXT = (
    "*이음* — 관심사 태그로 빠르게 친해지는 아이스브레이킹 봇\n"
    "• `/ieum 핑` — 봇이 살아있는지 확인\n"
    "• `/ieum 도움말` — 이 안내\n"
    "_태그 등록과 게임은 준비 중입니다._"
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


def build_command_reply(text: str | None) -> str:
    """커맨드 텍스트에 대한 응답 문구를 만든다(순수 함수).

    파싱과 응답 생성을 슬랙 SDK 바깥에 두어, 핸들러는 "받아서 그대로 ack"만 한다.
    """
    parsed = parse_command(text)
    if parsed.name == "ping":
        return "퐁 🏓"
    if parsed.name == "help":
        return HELP_TEXT
    return f"모르는 명령이에요: `{parsed.raw}`\n\n{HELP_TEXT}"


def register(app: AsyncApp) -> None:
    """Bolt 앱에 P0 핸들러를 붙인다."""

    @app.command("/ieum")
    async def handle_ieum(ack, command) -> None:
        # 응답을 ack 본문에 실어 보낸다 — response_url로 한 번 더 왕복하지 않아
        # 3초 제한에 가장 안전하다. 기본이 에페메랄이라 채널도 더럽히지 않는다.
        # (P2 이후 처리가 길어지는 커맨드는 `await ack()` 먼저 하고 respond로 나눈다.)
        await ack(text=build_command_reply(command.get("text")))

    @app.event("app_mention")
    async def handle_app_mention(event, say) -> None:
        # 스레드 안에서 멘션되면 같은 스레드로, 아니면 그 메시지에 스레드를 열어 답한다
        # — 채널 타임라인을 봇 응답으로 어지럽히지 않기 위해서다.
        await say(
            text=f"안녕하세요! `/ieum 도움말`로 시작해보세요.\n\n{HELP_TEXT}",
            thread_ts=event.get("thread_ts") or event.get("ts"),
        )
