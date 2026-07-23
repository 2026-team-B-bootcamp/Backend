"""DB 연결(엔진)과 세션 생성기, 그리고 모든 ORM 모델이 상속하는 Base 클래스를 정의.
models/ 아래의 모델들은 이 Base를 상속해서 실제 테이블과 매핑된다.
요청 흐름의 맨 끝단: 라우터 -> 서비스 -> 여기서 만든 세션으로 DB 접근.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


# 모든 테이블 모델(User 등)이 상속받는 공통 부모 클래스.
class Base(DeclarativeBase):
    pass


# DB와의 실제 연결을 관리하는 비동기 엔진(앱 전체에서 하나만 생성해 공유).
# pool_pre_ping: 풀에서 커넥션을 꺼낼 때마다 가벼운 ping으로 살아있는지 확인한다.
#   관리형 DB나 방화벽이 유휴 커넥션을 조용히 끊어도 첫 쿼리가 깨지지 않는다.
# pool_recycle: 커넥션을 30분마다 재생성해 서버측 idle timeout보다 먼저 교체한다.
engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_recycle=1800,
)

# 요청마다 이 팩토리로 새 세션을 만들어 쓴다(core/deps.py의 get_db 참고).
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
