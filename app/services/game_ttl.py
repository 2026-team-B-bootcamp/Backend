"""게임 상태의 Redis TTL 정책 — 대기/종료 상태로 방치된 판을 자동으로 걷어낸다.

문제: 누군가 게임을 열어놓고 아무도 안 들어오거나, 승부가 끝난 뒤 아무도 새 판을
시작하지 않으면 그 판이 몇십 분씩 남아 "대기 중"·"종료" 배지를 계속 띄웠다.
다음 사람은 남의 죽은 판을 치우고 시작해야 했다.

해결: 저장할 때 상태에 따라 TTL을 다르게 건다.
- 진행 중(playing): 원래의 긴 TTL. 오래 두는 게 맞다.
- 대기/종료: IDLE_TTL_SECONDS(30초). 그 안에 아무 일도 없으면 Redis가 키를 지우고,
  게임은 "없음" 상태로 돌아간다 = 강제 종료.

별도의 스위퍼 작업이 필요 없다는 게 이 방식의 장점이다. 활동이 있으면 저장이 일어나
TTL이 다시 30초로 갱신되므로, "마지막 활동 후 30초"라는 뜻이 된다. 워커가 여러 개여도
Redis 하나가 판정하므로 결과가 갈리지 않는다.
"""

# 대기·종료 상태로 이 시간을 넘기면 게임이 사라진다.
IDLE_TTL_SECONDS = 30

# 각 store가 쓰는 상태 문자열 (store들이 개별로 정의한 값과 같다).
_IDLE_STATUSES = frozenset({"waiting", "finished"})


def ttl_for(status: str, active_ttl: float) -> int:
    """상태에 맞는 TTL(초)을 고른다. 진행 중이면 active_ttl, 아니면 30초."""
    return IDLE_TTL_SECONDS if status in _IDLE_STATUSES else int(active_ttl)
