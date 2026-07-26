"""`/ieum` 슬래시 커맨드 텍스트 파싱.

슬랙 의존성이 전혀 없는 순수 함수만 모아둔다 — `handlers.py:1-6`의 원칙대로
커맨드 파싱을 슬랙 없이 테스트 가능하게 분리한 자리다. 슬랙 클라이언트나
DB에 접근하는 실제 핸들러는 `app.slack.handlers` 하위 모듈에 있다.
"""

from dataclasses import dataclass

from app.slack.features import Feature, catalog_text, find, suggest

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
    # 슬랙 안에서 끝나는 것들 — 웹으로 내보내지 않는다
    "태그등록": "tag_edit",
    "관심사등록": "tag_edit",
    "내태그": "tag_edit",
    "태그": "tag_view",
    "관심사": "tag_view",
    "말걸어줘": "icebreaker",
    "질문": "icebreaker",
    "아이스브레이커": "icebreaker",
}

HELP_TEXT = (
    "*Deverapo* — 관심사 태그로 빠르게 친해지는 아이스브레이킹 봇\n\n"
    "*슬랙에서 바로*\n"
    "• `/ieum 태그등록` — 내 관심사 적기\n"
    "• `/ieum 태그` — 누군가의 관심사 보기\n"
    "• `/ieum 말걸어줘` — AI가 말 걸 질문을 만들어줌\n\n"
    "*웹에서 같이 하기* (버튼 → 개인 입장 링크)\n"
    "• `/ieum 목록` — 아래 것들을 버튼으로\n"
    "• `/ieum 게임` — 이름을 타이핑하면 후보가 좁혀지는 선택기\n"
    "• `/ieum 게임 빙고` · `/ieum 같이보기` — 이름만 불러도 된다\n\n"
    + catalog_text()
    + "\n\n• `/ieum 핑` — 봇이 살아있는지 확인"
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


def suggestions_for(parsed: ParsedCommand) -> list[Feature]:
    """모르는 입력에 대해 "혹시 이거?"로 되물을 후보들.

    `게임`처럼 서브커맨드만 치고 만 경우엔 되물을 것이 없다(전체 선택기를 띄운다).
    """
    if parsed.name == "open":
        return suggest(parsed.args) if parsed.args else []
    return suggest(parsed.raw)
