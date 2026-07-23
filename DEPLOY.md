# Backend 배포 가이드

목표 구성: **EC2 단일 인스턴스 · Docker Compose(Caddy → FastAPI → Postgres/Redis) ·
DuckDNS 도메인 · GitHub Actions CD(release 브랜치)**.

전체 배경·리스크·비용은 레포 바깥의 `배포계획.md`에 있다. 이 문서는 손으로 실행하는
절차만 다룬다.

---

## 1. 브랜치 전략 (CD 트리거)

```
feature/*  --PR-->  main  --PR-->  release  --자동 배포-->  EC2
                     ↑                 ↑
                  ci.yml           deploy.yml
```

- `main` 머지로는 **배포되지 않는다.** 배포 시점을 사람이 고른다.
- 배포하려면 `main` → `release` PR을 만들어 머지한다. `deploy.yml`이 lint/test를
  다시 돌린 뒤 GHCR 이미지를 빌드하고 EC2에 배포한다.
- 롤백은 Actions > Deploy > *Run workflow* 에서 `image_tag`에 `sha-<이전커밋>`을
  넣고 실행한다(빌드·테스트를 건너뛰고 그 이미지를 바로 띄운다).

> ⚠️ 마이그레이션이 포함된 배포는 이미지 롤백만으로 되돌아가지 않는다. 컬럼 삭제·타입
> 변경 같은 파괴적 마이그레이션은 "추가 → 다음 배포에서 제거"의 2단계로 나눈다.

---

## 2. 사전 준비 (D-1)

| # | 항목 | 비고 |
|---|---|---|
| 1 | **Gemini 키 회전** | 작업 트리 `.env`에 실키가 노출된 이력이 있다. Google AI Studio에서 기존 키 revoke → 신규 발급. 커밋 금지 |
| 2 | **JWT_SECRET 생성** | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. 32자 미만이면 앱이 기동조차 안 된다 |
| 3 | **DuckDNS 서브도메인** | https://www.duckdns.org 에서 이름 생성 + 토큰 확보. IP는 EC2 만든 뒤 등록 |
| 4 | **AWS 키페어** | ap-northeast-2(서울). `.pem`은 시크릿 저장소로 공유 |
| 5 | **Vercel 계정** | Frontend 레포 import 권한 |

---

## 3. EC2 프로비저닝

| 항목 | 값 | 근거 |
|---|---|---|
| 리전 | `ap-northeast-2` | 사용자 위치 |
| 타입 | **t3.small** (2 vCPU / 2GB) | 컨테이너 4개. t3.micro는 부족 |
| 스토리지 | gp3 20GB | 이미지 + pgdata + 아바타 |
| OS | Ubuntu 22.04 LTS | |
| EIP | **할당** | 재부팅해도 IP가 유지돼야 DuckDNS를 다시 안 건다 |

**보안 그룹(인바운드)**

| 포트 | 소스 | 용도 |
|---|---|---|
| 22 | 팀원 IP `x.x.x.x/32` | SSH. `0.0.0.0/0` 금지 |
| 80 | `0.0.0.0/0` | Let's Encrypt HTTP-01 챌린지 + HTTPS 리다이렉트 |
| 443 | `0.0.0.0/0` | HTTPS / WSS |
| 5432 · 6379 · 8000 | **열지 않는다** | compose 내부 네트워크 전용 |

> CD가 GitHub Actions runner(IP 변동)에서 SSH로 붙는다. 22번을 팀원 IP로만 막아두면
> CD가 실패한다. 선택지는 두 가지다:
> - **간단**: 22번을 `0.0.0.0/0`으로 열고 비밀번호 로그인 비활성 + 키 인증만 허용
>   (Ubuntu 기본값) + fail2ban. 부트캠프 규모에서는 이 정도로 충분하다.
> - **안전**: AWS SSM Session Manager를 쓰고 22번을 완전히 닫는다(설정 부담 있음).

**셋업 스크립트** — 인스턴스 생성 후 SSH로 접속해 1회 실행한다.

```bash
sh scripts/ec2-bootstrap.sh   # 스왑 4GB · Docker · 로그 로테이션 · /opt/ieum
exit                          # docker 그룹 반영을 위해 재로그인 필수
```

---

## 4. DuckDNS 연결

EC2 Elastic IP가 나온 뒤에 한다.

```bash
# 브라우저에서 duckdns.org에 로그인해 IP를 직접 넣어도 되고, EC2에서 아래를 쳐도 된다.
# ip를 비우면 DuckDNS가 요청 출발지 IP(=EC2 공인 IP)를 자동으로 잡는다.
curl "https://www.duckdns.org/update?domains=<서브도메인>&token=<토큰>&ip="
# 응답이 OK 여야 한다.

dig +short <서브도메인>.duckdns.org    # EIP가 나오는지 확인
```

- Elastic IP를 쓰면 IP가 고정이라 갱신 크론이 필요 없다. EIP 없이 간다면
  `*/5 * * * * curl -s "https://www.duckdns.org/update?domains=...&token=...&ip="` 를 건다.
- `<이름>.duckdns.org`는 Public Suffix List에 등재돼 있어 Let's Encrypt 발급 한도가
  다른 duckdns 사용자와 섞이지 않는다.
- **DNS가 EIP를 가리키기 전에는 Caddy 인증서 발급이 실패한다.** 5절보다 먼저 한다.

---

## 5. 최초 수동 배포

CD를 붙이기 전에 손으로 한 번 성공시킨다.

```bash
cd /opt/ieum
git clone https://github.com/2026-team-B-bootcamp/Backend.git .
git checkout release          # /opt/ieum은 release 브랜치를 추적한다

cp .env.prod.example .env
chmod 600 .env
vi .env                       # 2절에서 만든 값 + SITE_ADDRESS + CORS_ORIGINS 채우기

# 최초에는 GHCR 이미지가 없을 수 있으므로 EC2에서 직접 빌드한다(수 분 소요).
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f api    # alembic upgrade head 확인
docker compose -f docker-compose.prod.yml logs caddy     # 인증서 발급 확인
```

### 검증 체크리스트

| # | 항목 | 방법 | 통과 기준 |
|---|---|---|---|
| V1 | TLS | `curl -I https://<도메인>/health` | 200 + 유효 인증서 |
| V2 | 마이그레이션 | api 로그 | 에러 없음, pgvector extension 생성됨 |
| V3 | 가입·로그인 | 프론트 또는 curl | JWT 발급 |
| V4 | WebSocket | 브라우저 두 창에서 같은 채널 채팅 | 상대 메시지 즉시 수신(wss) |
| V5 | **아바타 영속성** | 업로드 → `up -d --build` 재실행 → 재확인 | 이미지 유지 |
| V6 | AI 아이스브레이커 | 태그 등록 후 질문 생성 | Gemini 응답(stub 아님) |
| V7 | 태그 유사도 | pgvector 매칭 | 유사 태그 매칭 동작 |
| V8 | DB/Redis 비노출 | 외부에서 `nc -zv <EIP> 5432` | 연결 거부 |
| V9 | api 평문 비노출 | 외부에서 `curl http://<EIP>:8000/health` | 연결 거부 |

---

## 6. 프론트엔드(Vercel)

1. Vercel에서 `2026-team-B-bootcamp/Frontend` import. Framework preset **Vite** 자동 감지.
2. **Settings > Git > Production Branch를 `release`로 변경한다.** 백엔드와 배포
   시점을 맞추기 위해서다(기본값 `main`이면 머지 즉시 프로덕션이 바뀐다).
3. Environment Variables (**Production 스코프만** — Preview는 API를 붙이지 않는다):

   | Key | Value |
   |---|---|
   | `VITE_API_BASE_URL` | `https://<서브도메인>.duckdns.org` |
   | `VITE_GIPHY_KEY` | GIPHY 대시보드 값 |

   > Vite 환경변수는 **빌드 시점에 번들로 구워진다.** 값을 바꾸면 재배포해야 반영된다.
4. 배포된 주소(`https://<프로젝트>.vercel.app`)를 EC2 `.env`의 `CORS_ORIGINS`에 넣고
   `docker compose -f docker-compose.prod.yml up -d api`로 재기동한다.

> **커스텀 도메인은 붙이지 않는다.** DuckDNS는 A/AAAA 레코드만 지원하고 CNAME이 없어
> Vercel 커스텀 도메인 연결이 불가능하다. Vercel 기본 도메인도 고정 주소이므로
> `CORS_ORIGINS`에 박아두면 그만이다.
>
> Preview 배포에서는 실서버 API를 붙이지 않는다(로컬 백엔드 `http://localhost:8000`으로
> 개발·리뷰한다). `cors_origin_list`는 완전일치 목록이라 와일드카드가 안 되기 때문이다.

---

## 7. CD 활성화

**GitHub Secrets** (Settings > Secrets and variables > Actions > Secrets)

| Secret | 내용 |
|---|---|
| `EC2_HOST` | Elastic IP 또는 `<서브도메인>.duckdns.org` |
| `EC2_SSH_KEY` | 배포 전용 SSH **개인키 전문**. 키페어와 별도로 `ssh-keygen -t ed25519 -N ''`로 만들어 공개키를 EC2 `~/.ssh/authorized_keys`에 추가 |
| `GHCR_TOKEN` | **선택.** GHCR 패키지가 private일 때만 필요한 `read:packages` PAT |

> **GHCR_TOKEN을 안 만드는 쪽을 권한다.** GitHub > Packages > `backend` > Package settings >
> Change visibility 에서 패키지를 **Public**으로 바꾸면 EC2가 인증 없이 pull 한다.
> 레포가 이미 public이므로 이미지를 감출 실익이 없고, 회전·관리할 자격증명이 하나 줄어든다.
> `deploy.yml`은 `GHCR_TOKEN`이 비어 있으면 로그인을 건너뛰고 익명 pull 한다.

**GitHub Variables** (같은 화면 > Variables)

| Variable | 내용 |
|---|---|
| `SITE_ADDRESS` | `<서브도메인>.duckdns.org`. 배포 후 외부 스모크 테스트에 쓴다 |

**브랜치 보호 규칙**

- `main`: PR 필수, CI 통과 필수, 직접 push 금지
- `release`: PR 필수, 직접 push 금지 (실수로 배포되는 것을 막는다)

**첫 자동 배포 검증**: `release` 브랜치를 만들고 `main`에서 PR을 올려 머지 →
Actions에서 `Deploy` 워크플로가 ci → build → deploy → 스모크 테스트까지 초록인지 확인한다.

---

## 8. 운영

### 자주 쓰는 명령

```bash
cd /opt/ieum
C="docker compose -f docker-compose.prod.yml"
$C ps                              # 상태(healthy 여부 포함)
$C logs -f api                     # 로그
$C restart api                     # api만 재시작
$C exec db psql -U $POSTGRES_USER $POSTGRES_DB
$C exec redis redis-cli
$C exec api alembic current        # 마이그레이션 상태
$C exec api python scripts/seed_demo.py   # 데모 시딩
```

### 롤백

```bash
# 방법 1(권장): Actions > Deploy > Run workflow > image_tag = sha-<이전커밋>
# 방법 2(EC2에서 직접):
cd /opt/ieum
sed -i 's|^IMAGE_TAG=.*|IMAGE_TAG=sha-<이전커밋>|' .env
docker compose -f docker-compose.prod.yml up -d --no-build api
```

### 백업

```bash
mkdir -p /opt/ieum/backups
chmod +x /opt/ieum/scripts/backup.sh
crontab -e
# 0 3 * * * /opt/ieum/scripts/backup.sh >> /opt/ieum/backups/backup.log 2>&1
```

DB 덤프 + 아바타 볼륨을 14일 보관한다. 추가로 **EBS 스냅샷 자동화(AWS Data Lifecycle
Manager, 일 1회)** 를 켜면 인스턴스 통째 복구가 가능하다. **복구 리허설을 최소 1회**
해본다 — 복구해보지 않은 백업은 백업이 아니다.

### 모니터링

- 컨테이너 로그 로테이션은 `ec2-bootstrap.sh`가 `/etc/docker/daemon.json`에 설정한다(10MB × 3).
- Caddy 액세스 로그는 `caddylogs` 볼륨에 rolling 저장된다.
- `/health`를 UptimeRobot(무료, 5분 간격)에 등록해둔다.
- CloudWatch 기본 지표 + 디스크 사용률 80% 알람.

---

## 9. 컨테이너 구성 참고

- **외부 노출은 caddy(80/443)뿐이다.** api·db·redis는 호스트 포트를 열지 않는다.
- api는 비-root(`appuser`)로 실행되며, 엔트리포인트가 `alembic upgrade head`를 먼저
  돌린 뒤 uvicorn을 띄운다. 마이그레이션이 실패하면 컨테이너가 죽는다(스키마가
  안 맞는 채로 서버가 뜨는 것보다 낫다 — 의도된 fail-fast).
- 영속 볼륨은 `pgdata`(DB) · `redisdata`(AOF) · `avatars`(업로드) · `caddydata`(인증서)
  네 개다. **`caddydata`를 지우면 인증서를 재발급받게 되고 Let's Encrypt 한도에 걸릴 수 있다.**
- `docker-compose.prod.yml`의 api는 `image`와 `build`를 함께 갖는다. CD는 `pull`로
  GHCR 이미지를 받고, 수동 배포는 `--build`로 그 자리에서 빌드한다.
