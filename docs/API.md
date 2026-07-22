# API 명세

Base URL: `http://localhost:8000` · 인터랙티브 문서: `http://localhost:8000/docs` (Swagger UI)

## 공통 사항

- **인증**: `/auth/*`, `/health`를 제외한 모든 엔드포인트는 JWT 필요.
  요청 헤더에 `Authorization: Bearer <access_token>` 를 붙인다.
- 인증 실패 시 공통 응답 (401):

```json
{ "detail": "Could not validate credentials" }
```

- 요청 본문 검증 실패 시 공통 응답 (422):

```json
{
  "detail": [
    { "loc": ["body", "email"], "msg": "field required", "type": "missing" }
  ]
}
```

---

## 1. 인증 (Auth)

### POST /auth/signup — 회원가입

요청:

```json
{
  "email": "kim@example.com",
  "password": "secret1234",
  "display_name": "김철수"
}
```

응답 `201`:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

### POST /auth/login — 로그인

요청:

```json
{
  "email": "kim@example.com",
  "password": "secret1234"
}
```

응답 `200`: 회원가입과 동일한 `TokenResponse`.

---

## 2. 유저 (Users)

### GET /users/me — 내 정보 조회

응답 `200`:

```json
{
  "id": 1,
  "email": "kim@example.com",
  "display_name": "김철수",
  "avatar_url": "/static/avatars/1.png"
}
```

`avatar_url`은 업로드 전이면 `null`.

### PATCH /users/me — 내 정보 수정

요청:

```json
{
  "display_name": "김영희",
  "email": "younghee@example.com"
}
```

응답 `200`: `UserResponse` (위와 동일 형태).

### POST /users/me/avatar — 프로필 이미지 업로드

요청: `multipart/form-data`, 필드명 `file` (이미지 파일).

응답 `200`: `UserResponse` (`avatar_url` 갱신됨).

---

## 3. 서버 (Servers)

### POST /servers — 서버 생성

요청:

```json
{ "name": "부트캠프 B팀" }
```

응답 `201`:

```json
{
  "id": 3,
  "name": "부트캠프 B팀",
  "invite_code": "AB12CD34"
}
```

### GET /servers — 내가 속한 서버 목록

응답 `200`:

```json
[
  { "id": 3, "name": "부트캠프 B팀", "invite_code": "AB12CD34" },
  { "id": 5, "name": "스터디방", "invite_code": "ZX98YW76" }
]
```

### POST /servers/join — 초대코드로 서버 가입

요청:

```json
{ "invite_code": "AB12CD34" }
```

응답 `200`: 가입한 서버의 `ServerResponse`.

### GET /servers/{server_id}/members — 서버 멤버 목록

응답 `200` (`tags`는 해당 서버에서 등록한 태그, `common_with_me`는 나와 겹치는 태그):

```json
[
  {
    "user_id": 1,
    "display_name": "김철수",
    "tags": ["게임", "개발", "커피"],
    "common_with_me": ["개발"]
  },
  {
    "user_id": 2,
    "display_name": "이영희",
    "tags": [],
    "common_with_me": []
  }
]
```

### PUT /servers/{server_id}/tags — 내 태그 등록/수정 (서버별 3개 고정)

요청:

```json
{ "tag1": "게임", "tag2": "개발", "tag3": "커피" }
```

응답 `200`:

```json
{ "tags": ["게임", "개발", "커피"] }
```

---

## 4. 채널 (Channels)

### GET /servers/{server_id}/channels — 채널 목록

응답 `200`:

```json
[
  { "id": 10, "server_id": 3, "name": "일반" },
  { "id": 11, "server_id": 3, "name": "게임방" }
]
```

### POST /servers/{server_id}/channels — 채널 생성

요청:

```json
{ "name": "게임방" }
```

응답 `201`: `ChannelResponse` (위와 동일 형태).

---

## 5. 메시지 (Messages)

### POST /channels/{channel_id}/messages — 메시지 전송

요청:

```json
{ "content": "안녕하세요!" }
```

응답 `201`:

```json
{
  "id": 110,
  "user_id": 1,
  "display_name": "김철수",
  "tags": ["게임", "개발", "커피"],
  "content": "안녕하세요!",
  "created_at": "2026-07-22T09:30:00+00:00"
}
```

전송 성공 시 같은 채널의 WebSocket 접속자들에게 `message.new` 이벤트가 브로드캐스트된다.

### GET /channels/{channel_id}/messages — 메시지 목록

쿼리: `?after_id=110` (선택 — 해당 id 이후 메시지만 조회, 폴링용)

응답 `200`: `MessageOut` 배열 (위와 동일 형태).

---

## 6. WebSocket (실시간 채팅)

### WS /ws/channels/{channel_id}?token={access_token}

- 브라우저 WebSocket은 헤더를 못 붙이므로 토큰을 **쿼리 파라미터**로 전달.
- 종료 코드: `4401` 토큰 무효, `4403` 서버 멤버 아님.

클라이언트 → 서버 (타이핑 신호):

```json
{ "type": "typing" }
```

서버 → 클라이언트 이벤트:

```json
{ "type": "presence.update", "payload": { "users": [{ "user_id": 1, "display_name": "김철수" }] } }
```

```json
{ "type": "typing", "payload": { "user_id": 1, "display_name": "김철수" } }
```

```json
{
  "type": "message.new",
  "payload": {
    "id": 110,
    "user_id": 1,
    "display_name": "김철수",
    "tags": ["게임", "개발", "커피"],
    "content": "안녕하세요!",
    "created_at": "2026-07-22T09:30:00+00:00"
  }
}
```

---

## 7. 게임 — 빙고 (Bingo)

### POST /channels/{channel_id}/bingo/join — 참가

응답 `200`: `BingoStateResponse` (아래 GET과 동일).

### POST /channels/{channel_id}/bingo/click — 숫자 선택

요청:

```json
{ "number": 17 }
```

응답 `200`: `BingoStateResponse`.

### GET /channels/{channel_id}/bingo — 현재 상태 조회

응답 `200`:

```json
{
  "round": 1,
  "called_numbers": [17, 3, 22],
  "my_board": [
    [5, 17, 8, 21, 14],
    [3, 25, 11, 7, 19],
    [22, 9, 1, 16, 4],
    [13, 6, 24, 10, 18],
    [2, 20, 15, 23, 12]
  ],
  "players": [
    { "user_id": 1, "display_name": "김철수", "completed_lines": 2 },
    { "user_id": 2, "display_name": "이영희", "completed_lines": 1 }
  ],
  "winner_user_id": null
}
```

`my_board`는 아직 참가 전이면 `null`, `winner_user_id`는 3줄 완성자가 나오면 채워진다.

---

## 8. 게임 — 끝말잇기 (Wordchain)

### POST /channels/{channel_id}/wordchain/join — 참가

### POST /channels/{channel_id}/wordchain/start — 시작

### POST /channels/{channel_id}/wordchain/submit — 단어 제출

요청:

```json
{ "word": "사과" }
```

### GET /channels/{channel_id}/wordchain — 현재 상태 조회

위 4개 모두 응답 `200`은 `WordChainStateResponse`:

```json
{
  "status": "playing",
  "round": 1,
  "players": [
    { "user_id": 1, "display_name": "김철수", "alive": true },
    { "user_id": 2, "display_name": "이영희", "alive": false }
  ],
  "turn_user_id": 1,
  "words": [
    { "user_id": 2, "display_name": "이영희", "word": "기차" },
    { "user_id": 1, "display_name": "김철수", "word": "차표" }
  ],
  "winner_user_id": null,
  "seconds_left": 12,
  "last_event": "이영희 탈락 (시간 초과)"
}
```

`status`: `waiting` | `playing` | `finished`.

---

## 9. 게임 — 돌림판 (Wheel)

### POST /channels/{channel_id}/wheel/join — 참가

### POST /channels/{channel_id}/wheel/options — 항목 추가

요청:

```json
{ "label": "치킨" }
```

### DELETE /channels/{channel_id}/wheel/options/{option_id} — 항목 삭제

### POST /channels/{channel_id}/wheel/spin — 돌리기

### POST /channels/{channel_id}/wheel/reset — 초기화

### GET /channels/{channel_id}/wheel — 현재 상태 조회

응답 `200` (전부 `WheelStateResponse`):

```json
{
  "options": [
    { "id": 1, "label": "치킨", "added_by": "김철수" },
    { "id": 2, "label": "피자", "added_by": "이영희" }
  ],
  "result_option_id": 1,
  "spun_by": "김철수"
}
```

아직 안 돌렸으면 `result_option_id`, `spun_by`는 `null`.

---

## 10. 게임 — 사다리타기 (Ladder)

### POST /channels/{channel_id}/ladder/join — 참가

### POST /channels/{channel_id}/ladder/participants — 참가자 추가

요청:

```json
{ "label": "김철수" }
```

### DELETE /channels/{channel_id}/ladder/participants/{entry_id} — 참가자 삭제

### POST /channels/{channel_id}/ladder/results — 결과 항목 추가

요청:

```json
{ "label": "커피 쏘기" }
```

### DELETE /channels/{channel_id}/ladder/results/{entry_id} — 결과 항목 삭제

### POST /channels/{channel_id}/ladder/run — 실행

### POST /channels/{channel_id}/ladder/reset — 초기화

### GET /channels/{channel_id}/ladder — 현재 상태 조회

응답 `200` (전부 `LadderStateResponse`):

```json
{
  "status": "done",
  "participants": [
    { "id": 1, "label": "김철수", "added_by": "김철수" },
    { "id": 2, "label": "이영희", "added_by": "이영희" }
  ],
  "results": [
    { "id": 3, "label": "커피 쏘기", "added_by": "김철수" },
    { "id": 4, "label": "통과", "added_by": "김철수" }
  ],
  "rungs": [[0, 1], [0, 2]],
  "assignment": [1, 0],
  "run_by": "김철수"
}
```

실행 전에는 `rungs`, `assignment`, `run_by`가 `null`.
`assignment[i] = j` 는 "i번째 참가자 → j번째 결과" 매칭을 뜻한다.

---

## 11. 게임 — 오목 (Omok)

### POST /channels/{channel_id}/omok/join — 참가 (선착순 2인)

### POST /channels/{channel_id}/omok/place — 돌 놓기

요청 (0-indexed 좌표):

```json
{ "row": 7, "col": 7 }
```

### POST /channels/{channel_id}/omok/reset — 초기화

### GET /channels/{channel_id}/omok — 현재 상태 조회

응답 `200` (전부 `OmokStateResponse`):

```json
{
  "status": "playing",
  "board": [[0, 0, 0], [0, 1, 0], [0, 0, 2]],
  "players": [
    { "user_id": 1, "display_name": "김철수", "color": 1 },
    { "user_id": 2, "display_name": "이영희", "color": 2 }
  ],
  "turn": 2,
  "turn_user_id": 2,
  "winner_user_id": null,
  "winning_line": null,
  "last_move": [1, 1]
}
```

`board` 값: `0` 빈칸, `1` 흑, `2` 백 (실제 보드는 15×15 — 예시는 축약).
승부가 나면 `winner_user_id`와 `winning_line`(5목 좌표 배열)이 채워진다.

---

## 12. AI — 아이스브레이커

### POST /servers/{server_id}/members/{user_id}/icebreaker — 대화 질문 생성

대상 멤버의 태그를 바탕으로 대화 시작 질문을 생성한다.

응답 `200`:

```json
{ "question": "김철수님도 개발을 좋아하신다던데, 요즘 어떤 걸 만들고 계세요?" }
```

---

## 13. 기타

### GET /health — 헬스체크 (인증 불필요)

응답 `200`:

```json
{ "status": "ok" }
```
