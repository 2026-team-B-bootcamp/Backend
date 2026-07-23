"""초성퀴즈 API의 요청/응답 스키마(pydantic 모델).

routers/chosung.py가 요청 본문을 검증하고, store가 돌려준 게임 상태를
응답(JSON)으로 직렬화할 때 사용한다.
"""

from pydantic import BaseModel, Field


class ChosungSubmitRequest(BaseModel):
    word: str = Field(min_length=1, max_length=3)


class ChosungPlayerState(BaseModel):
    user_id: int
    display_name: str
    alive: bool


class ChosungStateResponse(BaseModel):
    status: str
    round: int = 1
    players: list[ChosungPlayerState]
    turn_user_id: int | None
    prompt: str | None
    words: list[str]
    loser_user_id: int | None
    seconds_left: int | None
    last_event: str | None
