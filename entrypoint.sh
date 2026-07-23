#!/bin/sh
# 프로덕션 컨테이너 엔트리포인트.
# 1) DB 스키마를 최신으로 올린다(pgvector extension·테이블 생성 포함).
#    실패하면(set -e) 컨테이너를 즉시 죽여서, 스키마가 안 맞는 채로 서버가
#    뜨는 상황을 막는다.
# 2) 마이그레이션이 끝나면 uvicorn을 --reload 없이 띄운다.
#    exec를 써서 uvicorn이 PID 1이 되게 한다(시그널 정상 전달·graceful shutdown).
set -e

echo "[entrypoint] alembic upgrade head..."
alembic upgrade head

echo "[entrypoint] starting uvicorn..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
