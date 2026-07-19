from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register models on Base.metadata)
from app.core.deps import get_db
from app.db.base import Base
from app.main import app
from app.services.game_registry import GameRegistry, get_game_registry


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
    # channel_id는 테스트마다 1부터 다시 매겨지므로, 게임 락도 테스트별로 새로 줘야
    # 앞 테스트에서 남은 락이 다음 테스트의 채널 id와 우연히 충돌하지 않는다.
    # (인스턴스를 클로저로 한 번만 만들어야 요청마다 같은 락 상태를 공유한다.)
    test_registry = GameRegistry()
    app.dependency_overrides[get_game_registry] = lambda: test_registry
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
