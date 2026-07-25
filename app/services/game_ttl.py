"""게임 상태의 Redis TTL 정책 — 방치된 판을 자동으로 걷어낸다.

문제: 누군가 게임을 열어놓고 아무도 안 들어오거나, 승부가 끝난 뒤 아무도 새 판을
시작하지 않으면 그 판이 몇십 분씩 남아 "대기 중"·"종료" 배지를 계속 띄웠다.
다음 사람은 남의 죽은 판을 치우고 시작해야 했다.

해결: 저장할 때 상태에 따라 TTL을 다르게 건다.
- 대기/종료: IDLE_TTL_SECONDS(30초). 판이 굴러가지 않는 상태라 짧게 끊는다.
- 진행 중(playing): ACTIVE_TTL_SECONDS(30분). 오래 두는 게 맞지만 무한정은 아니다 —
  둘 중 한 명이 브라우저를 닫고 사라진 판이 "🟢 진행중" 배지를 붙인 채 한 시간씩
  남아 있으면 다음 사람이 그 채널에서 같은 게임을 열 수 없다.

별도의 스위퍼 작업이 필요 없다는 게 이 방식의 장점이다. 활동이 있으면 저장이 일어나
TTL이 다시 채워지므로 "마지막 변화 이후 N초"라는 뜻이 된다. 워커가 여러 개여도
Redis 하나가 판정하므로 결과가 갈리지 않는다.
"""

# 대기·종료 상태로 이 시간을 넘기면 게임이 사라진다.
IDLE_TTL_SECONDS = 30

# 진행 중이어도 이 시간 동안 아무 변화(착수·호출·제출)가 없으면 강제 종료된다.
# 끝말잇기·초성퀴즈의 도화선(120초)이나 밸런스게임 마감(5분)보다 충분히 길어
# 정상적인 플레이를 끊지 않는다.
ACTIVE_TTL_SECONDS = 1800

# 각 store가 쓰는 상태 문자열 (store들이 개별로 정의한 값과 같다).
_IDLE_STATUSES = frozenset({"waiting", "finished"})


def ttl_for(status: str, active_ttl: float) -> int:
    """상태에 맞는 TTL(초)을 고른다.

    진행 중이면 store가 정한 TTL을 쓰되 ACTIVE_TTL_SECONDS(30분)로 상한을 둔다 —
    store마다 흩어진 TTL_SECONDS를 일일이 고치지 않고도 "30분 무변화 = 강제 종료"를
    한곳에서 보장하기 위해서다. 대기·종료면 30초.
    """
    if status in _IDLE_STATUSES:
        return IDLE_TTL_SECONDS
    return min(int(active_ttl), ACTIVE_TTL_SECONDS)
