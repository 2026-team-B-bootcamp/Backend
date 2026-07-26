"""채널별 틱택토(3×3, 3목) 게임 상태를 Redis에 저장하고 join/place/reset을 처리한다.

저장·검증·승리 판정 흐름 자체는 app/services/grid_game.py의 GridGameStore가
갖고 있다 (오목과 판 크기·승리 길이만 다를 뿐 나머지가 완전히 같다). 이 파일은
틱택토만의 값(판 크기 3, 3목, X/O, 점유 칸 문구)을 채워 넣는 얇은 래퍼다.
틱택토의 진영 표시자는 엔진 공통 필드 이름(mark)과 그대로 같아서, 오목과 달리
GridGame/GridGamePlayer를 감쌀 필요 없이 바로 재사용한다.
"""

from app.services.grid_game import GridGame, GridGamePlayer, GridGameStore
from app.services.grid_game import find_winning_line as _grid_find_winning_line

TTL_SECONDS = 3600
BOARD_SIZE = 3
WIN_LENGTH = 3

EMPTY = 0
X = 1  # 선공
O = 2  # noqa: E741 — 마크 상수(오목의 BLACK/WHITE와 대응)

WAITING = "waiting"
PLAYING = "playing"
FINISHED = "finished"

TicTacToePlayer = GridGamePlayer
TicTacToeGame = GridGame


def find_winning_line(
    board: list[list[int]], row: int, col: int, mark: int
) -> list[list[int]] | None:
    """방금 둔 (row, col)을 지나는 3목 줄이 있으면 그 좌표들을 돌려준다."""
    return _grid_find_winning_line(board, row, col, mark, BOARD_SIZE, WIN_LENGTH)


class TicTacToeStore(GridGameStore):
    def __init__(self, ttl_seconds: float = TTL_SECONDS) -> None:
        super().__init__(
            kind="tictactoe",
            board_size=BOARD_SIZE,
            win_length=WIN_LENGTH,
            first_mark=X,
            second_mark=O,
            occupied_message="이미 표시된 칸이에요",
            ttl_seconds=ttl_seconds,
        )


store = TicTacToeStore()


def get_tictactoe_store() -> TicTacToeStore:
    return store
