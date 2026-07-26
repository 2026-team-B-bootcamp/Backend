"""채널별 오목 게임 상태를 Redis에 저장하고 join/place(착수)/reset을 처리한다.

라우터(routers/omok.py)가 이 store를 호출해 대국을 열고 진행시킨다. 저장·
검증·승리 판정 흐름 자체는 오목과 틱택토가 거의 같아 app/services/grid_game.py의
GridGameStore가 갖고 있고, 이 파일은 오목만의 값(판 크기 15, 5목, 흑/백, 점유 칸
문구)을 채워 넣는 얇은 래퍼다. player.color처럼 오목에서만 쓰는 이름은 여기서
GridGamePlayer/GridGame을 살짝 감싸 얹는다.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.services.grid_game import GridGame, GridGamePlayer, GridGameStore
from app.services.grid_game import find_winning_line as _grid_find_winning_line

TTL_SECONDS = 3600
BOARD_SIZE = 15
WIN_LENGTH = 5

EMPTY = 0
BLACK = 1
WHITE = 2

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"


def find_winning_line(
    board: list[list[int]], row: int, col: int, color: int
) -> list[list[int]] | None:
    """방금 둔 (row, col) 돌을 지나는 5목 이상 줄이 있으면 그 좌표들을 돌려준다."""
    return _grid_find_winning_line(board, row, col, color, BOARD_SIZE, WIN_LENGTH)


@dataclass
class OmokPlayer(GridGamePlayer):
    @property
    def color(self) -> int:
        # 오목 쪽 코드/응답 스키마는 진영을 "color"라고 부른다 — 엔진 공통 필드인
        # mark를 그대로 가리키는 별칭일 뿐, 별도로 저장하지 않는다.
        return self.mark


class OmokGame(GridGame):
    def player_by_color(self, color: int) -> OmokPlayer | None:
        return self.player_by_mark(color)  # type: ignore[return-value]


class OmokStore(GridGameStore):
    game_cls = OmokGame
    player_cls = OmokPlayer

    def _migrate_player(self, p: dict) -> dict:
        """엔진 통합 이전에 저장된 판은 진영을 `color`로 적어뒀다.

        프로덕션 Redis는 appendonly라 배포를 넘겨 세션이 살아남는다. 옮겨주지 않으면
        진행 중이던 판이 `_load()`에서 TypeError로 죽고, 방장의 강제 종료마저
        `_load()`를 타므로 TTL 만료까지 그 채널 오목이 통째로 500이 된다.
        바로 아래 host_user_id를 setdefault로 채워주는 것과 같은 이유다.
        """
        if "color" not in p:
            return p
        migrated = {k: v for k, v in p.items() if k != "color"}
        migrated["mark"] = p["color"]
        return migrated

    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        # clock 파라미터는 실제로는 쓰이지 않는다(오목엔 타임아웃이 없다) —
        # tests/test_omok.py가 OmokStore(clock=FakeClock())로 생성하므로
        # 시그니처만 하위 호환으로 남겨둔다.
        super().__init__(
            kind="omok",
            board_size=BOARD_SIZE,
            win_length=WIN_LENGTH,
            first_mark=BLACK,
            second_mark=WHITE,
            occupied_message="이미 돌이 놓인 자리예요",
            ttl_seconds=ttl_seconds,
        )


store = OmokStore()


def get_omok_store() -> OmokStore:
    return store
