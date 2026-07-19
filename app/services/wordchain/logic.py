"""끝말잇기 단어 규칙: 한글 검사 + 두음법칙 허용 글자 계산.

services/wordchain/store.py의 submit()이 단어를 받을 때 이 모듈의 함수들을 호출해
"한글 단어인지", "이전 단어 끝글자로 이어지는지"를 판정한다.
"""

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3

_CHO_N = 2  # ㄴ
_CHO_L = 5  # ㄹ
_CHO_IEUNG = 11  # ㅇ

# 두음법칙에서 ㅇ으로 바뀌는 '이' 계열 중성 (ㅑ ㅒ ㅕ ㅖ ㅛ ㅠ ㅣ)
_I_JUNG = {2, 3, 6, 7, 12, 17, 20}

MIN_LENGTH = 2
MAX_LENGTH = 10


def _decompose(ch: str) -> tuple[int, int, int]:
    code = ord(ch) - _HANGUL_BASE
    return code // 588, (code % 588) // 28, code % 28


def _compose(cho: int, jung: int, jong: int) -> str:
    return chr(_HANGUL_BASE + cho * 588 + jung * 28 + jong)


def is_hangul_word(word: str) -> bool:
    if not (MIN_LENGTH <= len(word) <= MAX_LENGTH):
        return False
    return all(_HANGUL_BASE <= ord(ch) <= _HANGUL_LAST for ch in word)


def allowed_first_chars(prev_word: str) -> set[str]:
    """이전 단어의 마지막 글자 + 두음법칙 변형(례→예, 락→낙, 녀→여 등)."""
    last = prev_word[-1]
    cho, jung, jong = _decompose(last)
    allowed = {last}
    if cho == _CHO_L:
        allowed.add(_compose(_CHO_N, jung, jong))
        if jung in _I_JUNG:
            allowed.add(_compose(_CHO_IEUNG, jung, jong))
    elif cho == _CHO_N and jung in _I_JUNG:
        allowed.add(_compose(_CHO_IEUNG, jung, jong))
    return allowed
