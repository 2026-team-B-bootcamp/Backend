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
