# 작업 흐름

1. **이슈 생성** — 기능 단위 작업은 이슈부터 등록 (오탈자 수정처럼 사소한 건 생략 가능)
2. **브랜치 생성** — 이슈 번호를 포함해서 이름 짓기
   - 예: `feat/12-login-api`, `fix/12-login-bug`
3. **작업 후 PR 오픈** — base는 항상 `main`
   - PR 제목은 conventional commit 형식 (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`, `style:`, `perf:`) — 형식이 안 맞으면 자동 체크(`pr-title-lint`)가 실패함
   - 본문에 `Closes #이슈번호`를 적으면 머지될 때 이슈가 자동으로 닫힘
4. **리뷰 승인 후 머지**
   - `main`은 보호 브랜치라 PR 없이 직접 push 불가
   - 리뷰 승인 1명 이상 + CI 통과가 있어야 머지 가능 (관리자는 예외)
