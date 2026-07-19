"""앱 전역 설정값을 정의하는 파일.
.env 파일이나 환경변수에서 값을 읽어 Settings 객체 하나로 모아준다.
DB 접속 정보, JWT 비밀키 등 다른 모든 모듈이 여기서 값을 가져다 쓴다.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bootcamp:changeme@localhost:5432/bootcamp"
    jwt_secret: str = "dev-insecure-change-me-in-production-000"
    jwt_expire_minutes: int = 60 * 24
    algorithm: str = "HS256"

    @property
    def async_database_url(self) -> str:
        # database_url이 동기 드라이버(postgresql://) 형식으로 들어와도
        # 비동기 드라이버(postgresql+asyncpg://)로 강제 변환해준다.
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
