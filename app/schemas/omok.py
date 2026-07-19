"""오목 API 요청/응답 스키마 (routers/omok.py에서 사용)."""

from pydantic import BaseModel, Field


class OmokPlaceRequest(BaseModel):
    row: int = Field(ge=0, le=14)
    col: int = Field(ge=0, le=14)


class OmokPlayerState(BaseModel):
    user_id: int
    display_name: str
    color: int


class OmokStateResponse(BaseModel):
    status: str
    board: list[list[int]]
    players: list[OmokPlayerState]
    turn: int | None
    turn_user_id: int | None
    winner_user_id: int | None
    winning_line: list[list[int]] | None
    last_move: list[int] | None
