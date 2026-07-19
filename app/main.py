"""FastAPI 앱의 진입점(entry point).
서버 시작 시 이 파일이 실행되어 CORS 설정과 모든 라우터를 등록한다.
요청 흐름: 클라이언트 -> 여기서 등록된 라우터(routers/) -> 서비스(services/) -> 모델/DB(models/).
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db.base import engine
from app.routers import (
    ai,
    auth,
    bingo,
    ladder,
    messages,
    omok,
    servers,
    tags,
    users,
    wheel,
    wordchain,
    ws,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
(STATIC_DIR / "avatars").mkdir(parents=True, exist_ok=True)


# 앱 생명주기 관리 함수. yield 이전은 시작 시, 이후는 종료 시 실행된다.
# 여기서는 서버가 꺼질 때 DB 엔진 연결을 정리(dispose)한다.
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Rapport Tag Service", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 개발 편의: localhost 계열은 포트와 무관하게 허용 (Vite가 5173이 점유되면 5174+로 뜸)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 각 라우터를 앱에 연결한다. 예: auth.router는 "/auth/signup" 같은 경로를 처리한다.
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(servers.router)
app.include_router(tags.router)
app.include_router(bingo.router)
app.include_router(wordchain.router)
app.include_router(wheel.router)
app.include_router(ladder.router)
app.include_router(omok.router)
app.include_router(messages.router)
app.include_router(ai.router)
app.include_router(ws.router)


# 서버가 살아있는지 확인하는 헬스체크 엔드포인트.
@app.get("/health")
def health():
    return {"status": "ok"}
