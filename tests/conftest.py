import asyncio
import os
from collections.abc import AsyncGenerator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.redis as app_redis
import app.models  # noqa: F401  (register models on Base.metadata)
import app.services.ai.embedding as ai_embedding
from app.core.deps import get_db
from app.db.base import Base
from app.main import app

# 테스트는 실제 Postgres(pgvector)를 쓴다 — tag_embeddings의 vector 타입과
# 코사인 연산(<=>)은 SQLite로 흉내낼 수 없기 때문. 로컬에선 docker compose의
# db 컨테이너(localhost:5432)를 그대로 쓰되, 데이터 오염을 막기 위해 별도
# 테스트 DB(bootcamp_test)를 만들어 쓴다. 실행 전제: `docker compose up -d db`
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://bootcamp:changeme@localhost:5432/bootcamp_test",
)


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_database():
    """세션 시작 시 테스트 DB와 pgvector extension을 준비한다 (없으면 생성)."""

    async def _prepare() -> None:
        base_url, db_name = TEST_DATABASE_URL.rsplit("/", 1)
        # CREATE DATABASE는 트랜잭션 안에서 못 돌므로 AUTOCOMMIT으로 접속한다.
        admin = create_async_engine(f"{base_url}/postgres", isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": db_name},
                )
            ).scalar()
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await admin.dispose()

        engine = create_async_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await engine.dispose()

    asyncio.run(_prepare())


@pytest_asyncio.fixture(autouse=True)
async def fake_redis():
    """모든 테스트에서 전역 Redis 클라이언트를 fakeredis(인메모리 모조품)로 바꾼다.

    게임 세션·pub/sub·AI 캐시·rate limit이 전부 Redis를 쓰므로, 테스트마다
    새 fakeredis를 꽂아 상태를 격리한다 (실제 Redis 서버 없이 테스트 가능).
    코드가 get_redis()를 통해서만 클라이언트에 접근하기 때문에 이 교체가 먹힌다.
    """
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    original = app_redis.client
    app_redis.client = fake
    yield fake
    app_redis.client = original
    await fake.aclose()


@pytest.fixture(autouse=True)
def null_embedding(monkeypatch):
    """모든 테스트에서 임베딩 프로바이더를 Null로 고정한다.

    .env에 GEMINI_API_KEY가 있으면 태그 upsert가 실제 임베딩 API를 부르게
    되므로 기본은 차단한다 (완전일치 매칭만 동작). 유사도 매칭을 검증하는
    테스트는 이 싱글턴(_provider)을 가짜 프로바이더로 다시 덮어쓴다.
    """
    monkeypatch.setattr(ai_embedding, "_provider", ai_embedding.NullEmbeddingProvider())


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    # 테스트마다 스키마를 갈아엎어 격리한다 (이전 SQLite 인메모리 시절과 동일한 보장).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_engine) -> AsyncGenerator[AsyncClient, None]:
    session_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    # 게임 점유 상태(game_registry)는 이제 Redis에 있고, fake_redis 픽스처가
    # 테스트마다 새 인스턴스를 꽂으므로 테스트 간 격리가 저절로 된다.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _register(client: AsyncClient, email: str, password: str, display_name: str) -> str:
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": password, "display_name": display_name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    token = await _register(client, "alice@test.com", "pass1234", "Alice")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def register():
    return _register
