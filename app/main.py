from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.base import engine
from app.routers import ai, auth, bingo, ladder, messages, servers, tags, wheel, wordchain, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Rapport Tag Service", lifespan=lifespan)

# 개발 편의: localhost 계열은 포트와 무관하게 허용 (Vite가 5173이 점유되면 5174+로 뜸)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(servers.router)
app.include_router(tags.router)
app.include_router(bingo.router)
app.include_router(wordchain.router)
app.include_router(wheel.router)
app.include_router(ladder.router)
app.include_router(messages.router)
app.include_router(ai.router)
app.include_router(ws.router)


@app.get("/health")
def health():
    return {"status": "ok"}
