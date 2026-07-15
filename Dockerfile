FROM python:3.12-slim

# uv 설치 (공식 uv 이미지에서 바이너리만 복사)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 컨테이너는 root로 실행되는데, bind-mount(./:/app)로 인해 컨테이너가 쓰는
# 파일이 호스트에도 root 소유로 그대로 생긴다. .pyc 캐시 파일을 아예 안
# 만들게 해서 이 문제를 피한다.
ENV PYTHONDONTWRITEBYTECODE=1

# 가상환경을 /app 바깥에 둔다. docker-compose가 소스 폴더(./:/app)를 그대로
# bind-mount 하기 때문에, 가상환경이 /app 안에 있으면 빌드 시 설치한
# 의존성이 마운트로 가려져 사라져 버린다.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# 레이어 캐싱을 위해 의존성 파일만 먼저 복사해 설치한다.
# 소스 코드는 docker-compose가 bind-mount 하므로 여기서는 복사하지 않는다.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

ENV PATH="/opt/venv/bin:$PATH"

CMD ["uv", "run", "--no-sync", "uvicorn", "main:app", "--host", "0.0.0.0", "--reload"]
