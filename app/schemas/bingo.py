"""빙고 API 요청/응답 스키마 (routers/bingo.py에서 사용)."""

from pydantic import BaseModel


class ClickRequest(BaseModel):
    number: int


class PlayerState(BaseModel):
    user_id: int
    display_name: str
    completed_lines: int


class BingoStateResponse(BaseModel):
    called_numbers: list[int]
    my_board: list[int] | None
    players: list[PlayerState]
    winner_user_id: int | None = None
    round: int = 1
