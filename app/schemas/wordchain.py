"""끝말잇기 API의 요청/응답 스키마(pydantic 모델).

routers/wordchain.py가 요청 본문을 검증하고, store가 돌려준 게임 상태를
응답(JSON)으로 직렬화할 때 사용한다.
"""

from pydantic import BaseModel, Field


class WordSubmitRequest(BaseModel):
    word: str = Field(min_length=1, max_length=10)


class WordChainPlayerState(BaseModel):
    user_id: int
    display_name: str
    alive: bool


class WordEntryOut(BaseModel):
    user_id: int
    display_name: str
    word: str


class WordChainStateResponse(BaseModel):
    status: str
    players: list[WordChainPlayerState]
    turn_user_id: int | None
    words: list[WordEntryOut]
    winner_user_id: int | None
    seconds_left: int | None
    last_event: str | None
