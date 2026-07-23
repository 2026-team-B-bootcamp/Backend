"""틱택토 API 요청/응답 스키마 (routers/tictactoe.py에서 사용). 3×3 판, 3목 승리."""

from pydantic import BaseModel, Field


class TicTacToePlaceRequest(BaseModel):
    row: int = Field(ge=0, le=2)
    col: int = Field(ge=0, le=2)


class TicTacToePlayerState(BaseModel):
    user_id: int
    display_name: str
    mark: int  # 1 = X(선공), 2 = O


class TicTacToeStateResponse(BaseModel):
    status: str
    board: list[list[int]]
    players: list[TicTacToePlayerState]
    turn: int | None
    turn_user_id: int | None
    winner_user_id: int | None
    winning_line: list[list[int]] | None
    last_move: list[int] | None
