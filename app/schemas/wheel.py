"""돌림판 API의 요청/응답 스키마(pydantic 모델).

routers/wheel.py가 요청 본문을 검증하고, store가 돌려준 게임 상태를
응답(JSON)으로 직렬화할 때 사용한다.
"""

from pydantic import BaseModel


class AddWheelOptionRequest(BaseModel):
    label: str


class WheelOption(BaseModel):
    id: int
    label: str
    added_by: str


class WheelStateResponse(BaseModel):
    options: list[WheelOption]
    result_option_id: int | None
    spun_by: str | None
