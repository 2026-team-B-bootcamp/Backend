"""채널당 활성 미니게임을 1개로 제한하는 공용 레지스트리.

빙고/끝말잇기/돌림판/사다리는 각자 독립된 store를 쓰기 때문에, 한 채널에서
서로 다른 종류의 게임이 동시에 열리는 걸 막으려면 종류를 넘나드는 별도의
락이 필요하다. 게임을 새로 여는 시점(각 라우터의 join/get_or_create)에
acquire를 걸고, 그 라운드가 끝나는 시점에 release한다.

점유 상태는 "game:active:{채널id}" Redis 키에 저장한다 — 워커가 여러 개여도
같은 점유 상태를 보고, TTL을 걸어두므로 release가 유실돼도(서버 재시작 등)
채널이 영원히 잠기지 않는다.
"""

from fastapi import HTTPException, status

from app.core.redis import get_redis

GAME_LABELS: dict[str, str] = {
    "bingo": "빙고",
    "wordchain": "끝말잇기",
    "wheel": "돌림판",
    "ladder": "사다리타기",
    "omok": "오목",
}

# 게임 store들의 세션 TTL(1시간)과 맞춘다 — 세션이 만료되면 점유도 함께 풀린다.
TTL_SECONDS = 3600


class GameRegistry:
    def _key(self, channel_id: int) -> str:
        return f"game:active:{channel_id}"

    def _lock(self, channel_id: int):
        return get_redis().lock(f"lock:game-registry:{channel_id}", timeout=5)

    async def acquire(self, channel_id: int, kind: str) -> None:
        # 이 채널에 다른 종류의 게임이 이미 열려 있으면 409로 막고,
        # 비어 있거나 같은 종류면 이 게임 종류로 채널을 점유한다.
        async with self._lock(channel_id):
            r = get_redis()
            current = await r.get(self._key(channel_id))
            if current is not None and current != kind:
                label = GAME_LABELS.get(current, current)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"이미 '{label}' 게임이 진행 중이에요. 끝난 뒤에 시작할 수 있어요",
                )
            await r.set(self._key(channel_id), kind, ex=TTL_SECONDS)

    async def release(self, channel_id: int, kind: str) -> None:
        # 이 게임 종류가 실제로 채널을 점유하고 있을 때만 잠금을 해제한다.
        # (이미 다른 게임이 덮어썼다면 손대지 않는다.)
        async with self._lock(channel_id):
            r = get_redis()
            if await r.get(self._key(channel_id)) == kind:
                await r.delete(self._key(channel_id))


registry = GameRegistry()


def get_game_registry() -> GameRegistry:
    return registry
