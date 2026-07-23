"""빙고 API 요청/응답 스키마 (routers/bingo.py에서 사용)."""

from pydantic import BaseModel, Field


class ClickRequest(BaseModel):
    # 보드 숫자는 1~25뿐이다. 범위를 강제하지 않으면 임의의 정수를 called_numbers에
    # 무한정 쌓아 넣을 수 있다(TTL까지 누적, 남용). 범위 밖 값은 422로 거부한다.
    number: int = Field(ge=1, le=25)


class PlayerState(BaseModel):
    user_id: int
    display_name: str
    completed_lines: int


class BingoStateResponse(BaseModel):
    status: str
    called_numbers: list[int]
    my_board: list[int] | None
    players: list[PlayerState]
    winner_user_id: int | None = None
    round: int = 1
