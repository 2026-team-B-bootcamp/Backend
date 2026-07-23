# Backend 배포 메모

코드로 자동화할 수 없는, 사람이 직접 해야 하는 배포 절차만 모았다.

## 1. Gemini API 키 회전 (필수·즉시)

작업 트리의 `.env`에 실제 Gemini 키가 노출된 적이 있다. **이미 유출된 것으로
간주하고 반드시 폐기·재발급**한다.

1. Google AI Studio에서 기존 키를 삭제(revoke)한다.
2. 새 키를 발급한다.
3. 새 키는 로컬 `.env`에만 넣고(커밋 금지), 배포 플랫폼에는 시크릿으로 등록한다.

## 2. 배포 플랫폼 시크릿 설정

시크릿은 커밋된 `.env`가 아니라 플랫폼(대시보드/시크릿 매니저)이 주입한다.

백엔드(컨테이너 host):

- `JWT_SECRET` — 32자 이상 무작위 문자열. 미설정/기본값/짧으면 앱이 뜨지 않는다.
  생성: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `DATABASE_URL` — 예: `postgresql://user:pass@db-host:5432/dbname`
  (compose 내부 DB를 쓴다면 `POSTGRES_USER/PASSWORD/DB`만 넣어도 api가 조립한다.)
- `REDIS_URL` — 예: `redis://redis-host:6379/0`
- `GEMINI_API_KEY` — 위에서 재발급한 새 키
- `CORS_ORIGINS` — 허용할 프론트 출처(쉼표 구분 https). 예:
  `https://app.example.com,https://www.example.com`
  (비어 있으면 localhost 정규식으로 폴백하므로 프로덕션에서는 반드시 지정한다.)

프론트엔드(정적 빌드, `npm run build` 시점 주입):

- `VITE_API_BASE_URL` — 백엔드 API 주소(예: `https://api.example.com`)
- `VITE_GIPHY_KEY`(또는 `VITE_TENOR_KEY`) — 채팅 GIF 검색용(선택)

## 3. 프로덕션 compose 파일

프로덕션은 `docker-compose.prod.yml`을 쓴다(개발용 `docker-compose.yml` 아님):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

- 소스는 이미지에 구워진다(bind-mount·핫 리로드 없음).
- api 컨테이너 기동 시 엔트리포인트가 `alembic upgrade head`를 먼저 실행해
  스키마(pgvector extension·테이블)를 올린 뒤 uvicorn을 띄운다.
  마이그레이션이 실패하면 컨테이너가 죽는다(스키마 불일치 상태로 안 뜬다).
- api는 비-root(appuser)로 실행된다.

## 4. 포트 노출 변화(프로덕션)

- 프로덕션에서는 **db(5432)·redis(6379)의 호스트 포트를 열지 않는다** — compose
  내부 네트워크로만 접근한다. 외부에서 DB/Redis에 직접 붙을 수 없다.
- 개발용 pgadmin은 프로덕션 스택에서 제외했다.
- 외부로 노출되는 포트는 api(8000)뿐이다. 실제 서비스에서는 이 앞에
  리버스 프록시/로드밸런서(TLS 종단)를 두는 것을 권장한다.
