from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bootcamp:changeme@localhost:5432/bootcamp"
    jwt_secret: str = "dev-insecure-change-me-in-production-000"
    jwt_expire_minutes: int = 60 * 24
    algorithm: str = "HS256"

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
