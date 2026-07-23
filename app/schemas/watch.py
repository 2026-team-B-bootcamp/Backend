"""유튜브 Watch Together(동시 시청) API 요청/응답 스키마 (routers/watch.py에서 사용)."""

from pydantic import BaseModel, Field


class StartWatchRequest(BaseModel):
    # 유튜브 URL 또는 영상 ID. 서버가 ID만 뽑아 저장한다.
    url: str = Field(min_length=1, max_length=300)


class SyncWatchRequest(BaseModel):
    playing: bool
    position: float = Field(ge=0)


class WatchStateResponse(BaseModel):
    active: bool
    video_id: str | None = None
    playing: bool = False
    # 서버 기준으로 "지금 있어야 할" 재생 위치(초). 재생 중이면 경과 시간만큼 앞당겨 계산된다.
    position: float = 0.0
    host_user_id: int | None = None
    host_name: str | None = None
