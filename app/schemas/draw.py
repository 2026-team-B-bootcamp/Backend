"""공유 그림판(Whiteboard) API 요청/응답 스키마 (routers/draw.py에서 사용).

좌표는 캔버스 크기에 독립적이도록 0..1로 정규화된 값을 주고받는다 —
접속자마다 캔버스 크기가 달라도 같은 지점에 그려진다.
"""

from pydantic import BaseModel, Field, field_validator


class StrokeIn(BaseModel):
    # [[x, y], ...] 형태의 정규화(0..1) 좌표 목록. 각 점은 정확히 2개 원소.
    points: list[list[float]] = Field(min_length=1, max_length=2000)
    # #rrggbb 형식의 색상만 허용한다.
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    # 붓 굵기(px 기준). 과도한 값은 막는다.
    width: float = Field(ge=1, le=24)

    @field_validator("points")
    @classmethod
    def _each_point_is_xy(cls, v: list[list[float]]) -> list[list[float]]:
        for p in v:
            if len(p) != 2:
                raise ValueError("각 좌표는 [x, y] 두 값이어야 해요")
        return v


class StrokeOut(StrokeIn):
    # 누가 그린 획인지 — 브로드캐스트/저장 시 서버가 채워 넣는다.
    user_id: int


class DrawStateResponse(BaseModel):
    strokes: list[StrokeOut]
