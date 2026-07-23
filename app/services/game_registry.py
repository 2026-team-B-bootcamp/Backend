"""(구) 채널당 게임 1종 제한 → 폐기됨.

정책 변경: 한 채널에서 게임 '종류마다' 각각 1개씩 동시에 진행할 수 있다.
(빙고·오목·틱택토·밸런스·끝말잇기 등이 같은 채널에서 함께 열려 서로 관전 가능)
각 게임 store가 종류별 Redis 키로 독립 저장하므로 종류 간 잠금이 필요 없다.

기존 라우터들이 acquire/release를 호출하므로 인터페이스는 유지하되 동작은 no-op로 둔다.
"""


class GameRegistry:
    async def acquire(self, channel_id: int, kind: str) -> None:
        # 더 이상 채널을 한 게임으로 잠그지 않는다 — 종류별로 공존한다.
        return None

    async def release(self, channel_id: int, kind: str) -> None:
        return None


registry = GameRegistry()


def get_game_registry() -> GameRegistry:
    return registry
