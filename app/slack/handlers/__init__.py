"""슬랙 이벤트·커맨드 핸들러.

핵심 원칙 두 가지:
① 커맨드 문자열 파싱은 순수 함수(`parse_command`)로 분리한다 — 슬랙 없이 테스트 가능하게.
② 슬랙은 3초 안에 200을 못 받으면 재전송하므로 핸들러 첫 줄은 항상 `ack()`다.

기능(게임·같이보기·그림판…)마다 핸들러를 만들지 않는다. 슬랙이 하는 일은 어느
경우든 "개인 링크를 발급해 웹의 해당 패널을 열어주는 것" 하나뿐이라, features.py의
카탈로그를 읽는 경로 하나로 전부 처리한다.

이 파일 자체는 커맨드 파싱(`command_parser.py`), 신원·링크 발급(`identity.py`),
슬랙 액션 핸들러 3종(`handlers/command.py`, `handlers/entry.py`,
`handlers/tags_ai.py`, `handlers/misc.py`)으로 쪼개진 모듈들을 모아 등록만 한다.
테스트가 `app.slack.handlers`에서 옛 이름 그대로 가져다 쓸 수 있도록 아래에서
전부 re-export한다.
"""

from slack_bolt.async_app import AsyncApp

from app.slack.command_parser import (
    HELP_TEXT,
    ParsedCommand,
    parse_command,
    resolve_feature,
    suggestions_for,
    unknown_reply,
)
from app.slack.handlers.command import register_command
from app.slack.handlers.entry import register_entry
from app.slack.handlers.misc import register_misc
from app.slack.handlers.tags_ai import register_tags_ai
from app.slack.identity import (
    _display_name,
    _issue_entry_link,
    _load_tags,
    _resolve_target,
)

__all__ = [
    "HELP_TEXT",
    "ParsedCommand",
    "parse_command",
    "resolve_feature",
    "suggestions_for",
    "unknown_reply",
    "_display_name",
    "_issue_entry_link",
    "_load_tags",
    "_resolve_target",
    "register",
]


def register(app: AsyncApp) -> None:
    """Bolt 앱에 핸들러를 붙인다.

    등록 순서는 원래 handlers.py의 순서(커맨드 → 선택기/입장 → 태그·AI → 잡다)를
    그대로 지킨다. Bolt 라우팅이 등록 순서에 영향을 받을 수 있어서다.
    """
    register_command(app)
    register_entry(app)
    register_tags_ai(app)
    register_misc(app)
