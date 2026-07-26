"""끝말잇기(폭탄 돌리기) 게임 세션의 상태를 Redis에 저장하고 전이시키는 저장소.

폭탄 돌리기 엔진(도화선 타이머·턴 로테이션·라운드 전환·Redis 저장/락)은
services/bomb_game.py의 BombGameStore가 담당한다(초성퀴즈 store와 완전히 같은
엔진). 이 파일엔 끝말잇기 고유 규칙만 남는다 — 단어 형식(한글 2~10자) → 중복 여부
→ 앞 단어 끝글자 잇기(두음법칙 포함) 검증은 logic.py에 위임한다.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, status

from app.services.bomb_game import (
    FUSE_SECONDS,
    TTL_SECONDS,
    BombGame,
    BombGameStore,
    BombPlayer,
)
from app.services.wordchain.logic import allowed_first_chars, is_hangul_word


@dataclass
class WordEntry:
    user_id: int
    display_name: str
    word: str


@dataclass
class WordChainGame(BombGame):
    words: list[WordEntry] = field(default_factory=list)


class WordChainStore(BombGameStore[WordChainGame]):
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        fuse_seconds: float = FUSE_SECONDS,
        # 도화선 마감 시각이 Redis를 거쳐 워커 간에 공유되므로, 프로세스마다 기준이
        # 다른 monotonic 대신 벽시계(time.time)를 쓴다.
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__("wordchain", ttl_seconds, fuse_seconds, clock)

    def _new_game(self, channel_id: int) -> WordChainGame:
        return WordChainGame(channel_id=channel_id)

    def _reset_for_new_round(self, game: WordChainGame) -> None:
        game.words = []

    def _from_json(self, raw: str) -> WordChainGame:
        data = self._prepare_common(json.loads(raw))
        data["words"] = [WordEntry(**w) for w in data["words"]]
        return WordChainGame(**data)

    def _apply_start(self, game: WordChainGame, first: BombPlayer) -> str:
        return f"💣 폭탄 점화! {first.display_name}님부터 아무 단어나 시작하세요"

    def _validate_and_apply(self, game: WordChainGame, word: str, current: BombPlayer) -> str:
        # 단어 검증 3단계: 형식(한글 2~10자) → 중복 여부 → 앞 단어 끝글자 잇기.
        if not is_hangul_word(word):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="2~10글자 한글 단어를 입력하세요",
            )
        if word in game.used:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="이미 나온 단어예요",
            )
        if game.words:
            # 두음법칙 변형(예: 례→예)까지 포함해 허용 글자를 구해 비교한다.
            allowed = allowed_first_chars(game.words[-1].word)
            if word[0] not in allowed:
                pretty = "/".join(sorted(allowed))
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"'{pretty}'(으)로 시작하는 단어여야 해요",
                )

        # 정답이면 폭탄을 다음 사람에게 넘긴다. 도화선은 그대로 흐른다.
        game.words.append(
            WordEntry(user_id=current.user_id, display_name=current.display_name, word=word)
        )
        game.used.add(word)
        self._advance_turn(game)
        nxt = game.current_player()
        return f"'{word}' → 폭탄이 {nxt.display_name if nxt else '?'}님에게 넘어갔어요"


store = WordChainStore()


def get_wordchain_store() -> WordChainStore:
    return store
