from typing import Literal

from pydantic import BaseModel


class AddLadderEntryRequest(BaseModel):
    label: str


class LadderEntry(BaseModel):
    id: int
    label: str
    added_by: str


class LadderStateResponse(BaseModel):
    status: Literal["waiting", "revealed"]
    participants: list[LadderEntry]
    results: list[LadderEntry]
    rungs: list[list[bool]] | None
    assignment: list[int] | None
    run_by: str | None
