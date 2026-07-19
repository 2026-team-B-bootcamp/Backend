"""사다리타기 API의 요청/응답 스키마(pydantic 모델).

routers/ladder.py가 요청 본문을 검증하고, store가 돌려준 게임 상태를
응답(JSON)으로 직렬화할 때 사용한다.
"""

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
