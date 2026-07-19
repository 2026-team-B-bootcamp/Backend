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
