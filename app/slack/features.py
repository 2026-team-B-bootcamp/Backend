"""슬랙에서 열 수 있는 웹 기능 카탈로그.

웹에 기능이 하나 늘면 여기 한 줄만 추가하면 슬랙에서도 열린다 — 커맨드 파싱,
버튼, 링크 생성이 전부 이 표를 읽어서 동작하기 때문이다. 기능마다 핸들러를
따로 만들지 않는 이유이기도 하다: 슬랙이 하는 일은 어느 경우든 "개인 링크를
발급해 웹의 해당 패널을 열어주는 것" 하나뿐이다.

`page` 값은 웹의 전용 화면 경로(`/servers/{sid}/channels/{cid}/play/{page}`)다.
빈 문자열이면 전용 화면이 없다는 뜻으로, 채팅방 자체로 보낸다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    key: str  # 내부 식별자이자 버튼 value
    label: str  # 사람에게 보이는 이름
    emoji: str
    page: str  # 웹 전용 화면의 경로 조각. 빈 문자열이면 채팅방 자체가 목적지다.
    aliases: tuple[str, ...]  # 사용자가 칠 법한 표현들
    kind: str  # "game" | "tool" — 도움말을 묶어서 보여주는 용도


# 게임 6종. 웹의 게임 선택 패널(GamePip)에 있는 것과 1:1로 맞춘다.
_GAMES = (
    Feature("bingo", "빙고", "🎲", "bingo", ("빙고", "bingo"), "game"),
    Feature("wordchain", "끝말잇기", "🔤", "wordchain", ("끝말잇기", "wordchain"), "game"),
    Feature("omok", "오목", "⚫", "omok", ("오목", "omok"), "game"),
    Feature("tictactoe", "틱택토", "⭕", "tictactoe", ("틱택토", "삼목", "tictactoe"), "game"),
    Feature(
        "balance",
        "밸런스게임",
        "⚖️",
        "balance",
        ("밸런스게임", "밸런스", "balance"),
        "game",
    ),
    Feature("chosung", "초성퀴즈", "🔠", "chosung", ("초성퀴즈", "초성", "chosung"), "game"),
)

# 게임 외 기능. 링크로 여는 방식이 게임과 완전히 같아 같은 표에 둔다.
_TOOLS = (
    Feature(
        "watch",
        "같이보기",
        "📺",
        "watch",
        ("같이보기", "함께보기", "유튜브", "유툽", "watch"),
        "tool",
    ),
    Feature("draw", "그림판", "🎨", "draw", ("그림판", "그림", "화이트보드", "draw"), "tool"),
    Feature("members", "멤버", "👥", "members", ("멤버", "멤버목록", "members"), "tool"),
    # 관심사 태그 등록. 원래는 서버 첫 입장에 뜨는 모달인데, 슬랙에서 바로 부를 수 있게 한다.
    Feature("tags", "태그", "🏷️", "tags", ("태그", "태그등록", "관심사", "tags"), "tool"),
    # 전용 화면이 없는 유일한 항목 — 채팅방 자체가 목적지라 page가 비어 있다.
    Feature("chat", "채팅", "💬", "", ("채팅", "대화", "chat"), "tool"),
)

FEATURES: tuple[Feature, ...] = _GAMES + _TOOLS

GAMES: tuple[Feature, ...] = _GAMES
TOOLS: tuple[Feature, ...] = _TOOLS

_BY_KEY = {f.key: f for f in FEATURES}
# 별칭 → 기능. 소문자로 정규화해 담는다(영문 대소문자 구분 없이 받기 위해).
_BY_ALIAS = {alias.lower(): f for f in FEATURES for alias in f.aliases}


def by_key(key: str) -> Feature | None:
    """버튼 value(=key)로 기능을 찾는다."""
    return _BY_KEY.get(key)


def find(word: str) -> Feature | None:
    """사용자가 친 단어로 기능을 찾는다. 못 찾으면 None."""
    return _BY_ALIAS.get(word.strip().lower())


def catalog_text() -> str:
    """도움말에 넣을 기능 목록. 게임과 도구를 나눠 한 줄씩."""
    games = "  ".join(f"{f.emoji} {f.label}" for f in GAMES)
    tools = "  ".join(f"{f.emoji} {f.label}" for f in TOOLS)
    return f"*게임*  {games}\n*그 외*  {tools}"
