"""초성퀴즈(폭탄 돌리기) 게임 세션의 상태를 Redis에 저장하고 전이시키는 저장소.

routers/chosung.py의 각 엔드포인트가 호출하는 진입점이며, 단어 자체의 유효성
검사(한글·초성 일치)는 logic.py에 위임한다. 폭탄 돌리기 엔진(도화선 타이머·턴
로테이션·라운드 전환·Redis 저장/락)은 services/bomb_game.py의 BombGameStore가
담당한다(끝말잇기 store와 완전히 같은 엔진). 이 파일엔 초성퀴즈 고유 규칙만 남는다.
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
from app.services.chosung.logic import initials, is_hangul_word, random_prompt


@dataclass
class ChosungGame(BombGame):
    # 현재 폭탄 든 사람이 풀어야 할 초성 문제(예: 'ㅅㄱ').
    prompt: str | None = None
    # 지금까지 제출된 정답 단어들(표시용 히스토리).
    words: list[str] = field(default_factory=list)


class ChosungStore(BombGameStore[ChosungGame]):
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        fuse_seconds: float = FUSE_SECONDS,
        # 도화선 마감 시각이 Redis를 거쳐 워커 간에 공유되므로, 프로세스마다 기준이
        # 다른 monotonic 대신 벽시계(time.time)를 쓴다.
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__("chosung", ttl_seconds, fuse_seconds, clock)

    def _new_game(self, channel_id: int) -> ChosungGame:
        return ChosungGame(channel_id=channel_id)

    def _reset_for_new_round(self, game: ChosungGame) -> None:
        game.words = []
        game.prompt = None

    def _from_json(self, raw: str) -> ChosungGame:
        data = self._prepare_common(json.loads(raw))
        return ChosungGame(**data)

    def _apply_start(self, game: ChosungGame, first: BombPlayer) -> str:
        game.prompt = random_prompt()
        return f"💣 폭탄 점화! {first.display_name}님부터 초성을 맞혀 넘기세요"

    def _validate_and_apply(self, game: ChosungGame, word: str, current: BombPlayer) -> str:
        # 단어 검증: 형식(한글 2~3자) → 길이 일치 → 초성 글자별 일치 → 중복 여부.
        # 정답에 대한 사전 검사는 하지 않는다(초성이 프롬프트와 맞으면 통과).
        prompt = game.prompt or ""
        if not is_hangul_word(word):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="한글 단어를 입력하세요",
            )
        if len(word) != len(prompt) or initials(word) != prompt:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"초성 '{prompt}'에 맞는 단어여야 해요",
            )
        if word in game.used:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="이미 나온 단어예요",
            )

        # 정답이면 폭탄을 다음 사람에게 넘기고 새 문제를 낸다. 도화선은 그대로.
        game.words.append(word)
        game.used.add(word)
        self._advance_turn(game)
        game.prompt = random_prompt()
        nxt = game.current_player()
        return (
            f"{current.display_name}님이 '{word}' 정답! 폭탄이 "
            f"{nxt.display_name if nxt else '?'}님에게 넘어갔어요"
        )


store = ChosungStore()


def get_chosung_store() -> ChosungStore:
    return store
