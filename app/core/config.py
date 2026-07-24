"""앱 전역 설정값을 정의하는 파일.
.env 파일이나 환경변수에서 값을 읽어 Settings 객체 하나로 모아준다.
DB 접속 정보, JWT 비밀키 등 다른 모든 모듈이 여기서 값을 가져다 쓴다.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 절대 실서비스에서 쓰면 안 되는 값 — 과거 소스에 하드코딩돼 있던 기본 시크릿.
# 이 값(또는 빈 값/짧은 값)으로는 앱이 아예 뜨지 않게 막는다(토큰 위조 방지).
_INSECURE_JWT_SECRET = "dev-insecure-change-me-in-production-000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bootcamp:changeme@localhost:5432/bootcamp"
    # 실시간 브로드캐스트(pub/sub)·게임 세션(TTL)·AI 캐시가 모두 이 Redis를 쓴다.
    redis_url: str = "redis://localhost:6379/0"
    # 기본값 없음 — .env의 JWT_SECRET(예: `python -c "import secrets;
    # print(secrets.token_urlsafe(48))"`)을 반드시 설정해야 앱이 뜬다.
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60 * 24
    algorithm: str = "HS256"

    # 허용할 프론트 출처 목록(쉼표 구분). 비어 있으면(로컬 개발 기본값) main.py가
    # localhost 정규식 방식으로 폴백한다. 배포 시 정확한 https 출처를 넣으면
    # 그 목록만 명시적으로 허용한다. 예: "https://app.example.com,https://www.example.com"
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        # 쉼표로 구분된 문자열을 공백을 정리한 리스트로 변환한다.
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("jwt_secret")
    @classmethod
    def _reject_weak_jwt_secret(cls, value: str) -> str:
        # 시작 시점에 강제로 검증한다 — 약한 시크릿이면 서버가 뜨지 않는다.
        if not value or value == _INSECURE_JWT_SECRET or len(value) < 32:
            raise ValueError(
                "JWT_SECRET이 설정되지 않았거나 안전하지 않습니다. .env에 32자 이상의 "
                "무작위 문자열을 설정하세요. 예: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return value

    # Gemini 아이스브레이커 설정. 키가 비어 있으면 stub 템플릿으로 동작한다.
    # 모델명은 "-latest" 별칭 대신 고정 — 별칭은 구글이 뒤에서 모델을 바꿀 수 있다.
    # flash-lite인 이유: 무료 티어 한도가 flash(5 RPM·20 RPD)보다 훨씬 넉넉
    # (15 RPM·500 RPD)하고, 한 줄 질문 생성엔 품질 차이가 없다.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"

    # 유사 태그 매칭(pgvector) 설정. 태그 텍스트를 Gemini 임베딩으로 벡터화해
    # 코사인 유사도가 임계값 이상이면 완전일치와 똑같이 "겹치는 관심사"로 본다.
    gemini_embedding_model: str = "gemini-embedding-001"
    # 768: MRL 축소 차원. 짧은 태그 매칭엔 3072 풀 차원과 품질 차이가 없고
    # 저장 공간이 1/4이다. 바꾸면 기존 tag_embeddings 데이터와 호환되지 않는다.
    tag_embedding_dim: int = 768
    # 기준 쌍 실측으로 정한 값 (gemini-embedding-001, SEMANTIC_SIMILARITY, 768차원).
    # 태그는 "롤 티어 실버4", "맛집탐방(특히 라멘맛집)" 같은 문구형이 기본이라
    # 문구 쌍으로 보정했다: 유사 쌍(롤 티어 실버4↔롤 골드 승급전 중 0.877,
    # 포켓몬 시리즈 전부 클리어↔피카츄 굿즈 모음 0.844)은 0.84 이상,
    # 무관 쌍(보드게임 좋아함↔여행 계획 짜는 게 취미 0.834)은 미만으로 갈렸다.
    # 문구형은 단어형("포켓몬"↔"피카츄" 0.92)보다 전반적으로 낮게 나오므로
    # 단어 태그만 쓴다면 0.86까지 올려도 된다. .env로 조정 가능.
    tag_similarity_threshold: float = 0.84

    # 슬랙 봇 설정. jwt_secret과 달리 필수 검증을 걸지 않는다 — 슬랙 값이 없어도
    # 기존 웹 서비스는 그대로 떠야 하고, main.py가 slack_enabled일 때만 라우터를 단다.
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    # 봇이 발급하는 입장 링크의 기준 주소(프론트). 끝 슬래시 없이.
    public_web_url: str = "http://localhost:5173"

    @property
    def slack_enabled(self) -> bool:
        # 토큰과 서명 시크릿이 둘 다 있어야 슬랙 엔드포인트를 연다.
        # 하나만 있으면 서명 검증이 불가능하거나 응답을 못 보내므로 켜지 않는다.
        return bool(self.slack_bot_token and self.slack_signing_secret)

    @property
    def async_database_url(self) -> str:
        # database_url이 동기 드라이버(postgresql://) 형식으로 들어와도
        # 비동기 드라이버(postgresql+asyncpg://)로 강제 변환해준다.
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
