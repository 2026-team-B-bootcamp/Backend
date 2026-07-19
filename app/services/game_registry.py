"""채널당 활성 미니게임을 1개로 제한하는 공용 레지스트리.

빙고/끝말잇기/돌림판/사다리는 각자 독립된 store를 쓰기 때문에, 한 채널에서
서로 다른 종류의 게임이 동시에 열리는 걸 막으려면 종류를 넘나드는 별도의
락이 필요하다. 게임을 새로 여는 시점(각 라우터의 join/get_or_create)에
acquire를 걸고, 그 라운드가 끝나는 시점에 release한다.
"""

import asyncio

from fastapi import HTTPException, status

GAME_LABELS: dict[str, str] = {
    "bingo": "빙고",
    "wordchain": "끝말잇기",
    "wheel": "돌림판",
    "ladder": "사다리타기",
}


class GameRegistry:
    def __init__(self) -> None:
        self._active: dict[int, str] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, channel_id: int, kind: str) -> None:
        async with self._lock:
            current = self._active.get(channel_id)
            if current is not None and current != kind:
                label = GAME_LABELS.get(current, current)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"이미 '{label}' 게임이 진행 중이에요. 끝난 뒤에 시작할 수 있어요",
                )
            self._active[channel_id] = kind

    async def release(self, channel_id: int, kind: str) -> None:
        async with self._lock:
            if self._active.get(channel_id) == kind:
                del self._active[channel_id]


registry = GameRegistry()


def get_game_registry() -> GameRegistry:
    return registry
