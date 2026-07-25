"""슬랙에서 열 수 있는 웹 기능 카탈로그.

웹에 기능이 하나 늘면 여기 한 줄만 추가하면 슬랙에서도 열린다 — 커맨드 파싱,
버튼, 링크 생성이 전부 이 표를 읽어서 동작하기 때문이다. 기능마다 핸들러를
따로 만들지 않는 이유이기도 하다: 슬랙이 하는 일은 어느 경우든 "개인 링크를
발급해 웹의 해당 패널을 열어주는 것" 하나뿐이다.

`page` 값은 웹의 전용 화면 경로(`/servers/{sid}/channels/{cid}/play/{page}`)다.

여기 있는 것은 전부 "웹에서 여럿이 같이 하는 것"이다. 태그 등록·조회와 AI
아이스브레이커는 슬랙 안에서 끝나므로 이 표에 없다(handlers.py 참고).
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
)

# 슬랙에서 웹으로 넘어가는 것은 "같이 하는 것"뿐이다. 채팅·멤버 목록·태그 조회처럼
# 혼자 보는 화면까지 링크로 내보내면, 슬랙에서 이미 대화 중인 사람을 굳이 브라우저로
# 쫓아내는 셈이 된다. 그런 것들은 슬랙 안에서 끝낸다(handlers.py의 태그·말걸어줘).

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


def search(query: str, limit: int = 100) -> list[Feature]:
    """타이핑한 조각으로 기능을 좁힌다 — 선택기 타이핑 자동완성(external_select)용.

    `find`가 완전일치만 보는 것과 달리 여기는 부분일치를 받는다. "빙"만 쳐도
    빙고가 남아야 자동완성이라 할 수 있기 때문이다. 빈 문자열이면 전부 돌려준다
    (선택기를 열자마자 목록이 보이게).

    앞부분이 맞는 것을 먼저 둔다 — "오"를 쳤을 때 '오목'이 '초성퀴즈'(alias에
    'chosung'이 있어 o를 포함)보다 위에 와야 자연스럽다.
    """
    q = query.strip().lower()
    if not q:
        return list(FEATURES)[:limit]

    prefix: list[Feature] = []
    contains: list[Feature] = []
    for f in FEATURES:
        # 사람에게 보이는 이름도 검색 대상이다 — alias에 없는 표기로 칠 수 있다.
        candidates = (f.label.lower(), *(a.lower() for a in f.aliases))
        if any(c.startswith(q) for c in candidates):
            prefix.append(f)
        elif any(q in c for c in candidates):
            contains.append(f)
    return (prefix + contains)[:limit]


def suggest(word: str, limit: int = 3) -> list[Feature]:
    """오타로 못 찾았을 때 "혹시 이거?"로 되물을 후보를 고른다.

    부분일치(search)를 먼저 보고, 그래도 없으면 편집거리로 가까운 별칭을 찾는다.
    '빙고게임'처럼 덧붙인 말은 부분일치가, '빙곰'처럼 한 글자 틀린 것은
    편집거리가 잡아준다.
    """
    hits = search(word, limit)
    if hits:
        return hits

    import difflib

    # cutoff이 기본값(0.6)이면 '빙곰'↔'빙고'처럼 두 글자 중 한 글자만 틀려도
    # 비율이 0.5라 떨어진다. 한국어 기능 이름은 2~4글자라 한 글자 오타의 비중이
    # 크므로 기준을 낮춘다 — "혹시 이거?"로 되묻는 자리라 헛짚어도 손해가 적다.
    close = difflib.get_close_matches(
        word.strip().lower(), list(_BY_ALIAS.keys()), n=limit, cutoff=0.5
    )
    # 별칭 여러 개가 같은 기능을 가리킬 수 있으므로 중복을 없앤다(순서 유지).
    seen: dict[str, Feature] = {}
    for alias in close:
        f = _BY_ALIAS[alias]
        seen.setdefault(f.key, f)
    return list(seen.values())
