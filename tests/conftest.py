from collections.abc import AsyncGenerator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.redis as app_redis
import app.models  # noqa: F401  (register models on Base.metadata)
from app.core.deps import get_db
from app.db.base import Base
from app.main import app


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


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
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
