"""앱 전역 설정값을 정의하는 파일.
.env 파일이나 환경변수에서 값을 읽어 Settings 객체 하나로 모아준다.
DB 접속 정보, JWT 비밀키 등 다른 모든 모듈이 여기서 값을 가져다 쓴다.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bootcamp:changeme@localhost:5432/bootcamp"
    # 실시간 브로드캐스트(pub/sub)·게임 세션(TTL)·AI 캐시가 모두 이 Redis를 쓴다.
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "dev-insecure-change-me-in-production-000"
    jwt_expire_minutes: int = 60 * 24
    algorithm: str = "HS256"

    # Gemini 아이스브레이커 설정. 키가 비어 있으면 stub 템플릿으로 동작한다.
    # 모델명은 "-latest" 별칭 대신 고정 — 별칭은 구글이 뒤에서 모델을 바꿀 수 있다.
    # flash-lite인 이유: 무료 티어 한도가 flash(5 RPM·20 RPD)보다 훨씬 넉넉
    # (15 RPM·500 RPD)하고, 한 줄 질문 생성엔 품질 차이가 없다.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"

    @property
    def async_database_url(self) -> str:
        # database_url이 동기 드라이버(postgresql://) 형식으로 들어와도
        # 비동기 드라이버(postgresql+asyncpg://)로 강제 변환해준다.
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
