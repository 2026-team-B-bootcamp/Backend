"""빙고 판 생성 및 승리(줄 완성) 판정 규칙.

라우터(routers/bingo.py)와 store(services/bingo/store.py)가 이 모듈의
generate_board/count_completed_lines를 호출해 새 판을 만들고 각 플레이어의
완성 줄 수를 계산한다.
"""

import random

GRID_SIZE = 5
CELL_COUNT = GRID_SIZE * GRID_SIZE
NUMBER_RANGE = 25
WIN_LINES = 3


def _build_lines() -> list[list[int]]:
    # 5x5 판을 1차원 인덱스(0~24)로 다룬다: 가로 5줄 + 세로 5줄 + 대각선 2줄,
    # 총 12개 줄의 인덱스 조합을 미리 계산해 LINES에 캐싱해둔다.
    lines: list[list[int]] = []
    for row in range(GRID_SIZE):
        lines.append([row * GRID_SIZE + col for col in range(GRID_SIZE)])
    for col in range(GRID_SIZE):
        lines.append([row * GRID_SIZE + col for row in range(GRID_SIZE)])
    lines.append([i * GRID_SIZE + i for i in range(GRID_SIZE)])
    lines.append([i * GRID_SIZE + (GRID_SIZE - 1 - i) for i in range(GRID_SIZE)])
    return lines


LINES: list[list[int]] = _build_lines()


def generate_board() -> list[int]:
    # 1~25 숫자를 무작위로 섞어 플레이어 개인 보드로 사용한다.
    numbers = list(range(1, NUMBER_RANGE + 1))
    random.shuffle(numbers)
    return numbers


def count_completed_lines(board: list[int], called_numbers: set[int]) -> int:
    # 호출된 번호와 겹치는 칸을 표시한 뒤, 한 줄(LINES)이 전부 표시됐는지로
    # 완성된 줄 수를 센다. 이 값이 WIN_LINES 이상이면 빙고 승리.
    marked = [cell in called_numbers for cell in board]
    return sum(1 for line in LINES if all(marked[i] for i in line))
