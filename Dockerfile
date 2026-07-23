FROM python:3.12-slim

# uv 설치 (공식 uv 이미지에서 바이너리만 복사)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# .pyc 캐시 파일을 안 만들고, 로그를 버퍼링 없이 바로 내보낸다(컨테이너 로그 즉시 확인용).
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 가상환경을 /app 바깥(/opt/venv)에 둔다. 개발 compose가 소스 폴더(./:/app)를
# 그대로 bind-mount 하기 때문에, 가상환경이 /app 안에 있으면 마운트로 가려져
# 사라진다. 프로덕션에서도 위치를 동일하게 유지해 개발/운영 경로를 맞춘다.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# 레이어 캐싱을 위해 의존성 파일만 먼저 복사해 설치한다.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

ENV PATH="/opt/venv/bin:$PATH"

# 프로덕션 이미지는 소스를 이미지 안에 굽는다(bind-mount에 의존하지 않는다).
# 개발 compose는 이 위에 ./:/app를 bind-mount 하고 command를 덮어써서
# 핫 리로드로 동작한다(docker-compose.yml 참고).
COPY . .

# 마이그레이션 후 서버를 띄우는 엔트리포인트. 이미지 빌드 시점에 실행 권한을 준다.
RUN chmod +x /app/entrypoint.sh

# root가 아닌 전용 사용자로 실행한다(권한 최소화). /app과 가상환경 소유권을 넘긴다.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /opt/venv
USER appuser

EXPOSE 8000

# 기본(프로덕션) 실행: 마이그레이션 → uvicorn(리로드 없음). 개발 compose는 이 CMD를
# command:로 덮어써 --reload 개발 서버로 되돌린다.
ENTRYPOINT ["/app/entrypoint.sh"]
