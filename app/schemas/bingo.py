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
