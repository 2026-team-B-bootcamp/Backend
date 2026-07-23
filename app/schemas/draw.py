"""공유 그림판(Whiteboard) API 요청/응답 스키마 (routers/draw.py에서 사용).

좌표계는 space 필드가 정한다:
- "px"  : 캔버스 좌상단 기준 CSS 픽셀 좌표. 창을 키우면 그림이 커지는 게 아니라
          "그릴 수 있는 면"이 넓어진다 — 현재 클라이언트가 쓰는 좌표계다.
- "norm": 0..1 정규화 좌표(구버전). 창 크기에 맞춰 그림 전체가 늘어난다.

Redis에 이미 쌓여 있던 예전 획에는 space 키가 없으므로 기본값을 "norm"으로 둔다 —
그래야 배포 직후에도 옛 획이 원래 모양대로 다시 그려진다.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class StrokeIn(BaseModel):
    # [[x, y], ...] 형태의 좌표 목록(해석은 space에 따름). 각 점은 정확히 2개 원소.
    points: list[list[float]] = Field(min_length=1, max_length=2000)
    # #rrggbb 형식의 색상만 허용한다.
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    # 붓 굵기(px 기준). 과도한 값은 막는다.
    width: float = Field(ge=1, le=24)
    # 좌표계. 구버전 데이터/클라이언트 호환을 위해 생략 시 "norm"으로 본다.
    space: Literal["px", "norm"] = "norm"

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
