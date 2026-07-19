import random

GRID_SIZE = 5
CELL_COUNT = GRID_SIZE * GRID_SIZE
NUMBER_RANGE = 25
WIN_LINES = 3


def _build_lines() -> list[list[int]]:
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
    numbers = list(range(1, NUMBER_RANGE + 1))
    random.shuffle(numbers)
    return numbers


def count_completed_lines(board: list[int], called_numbers: set[int]) -> int:
    marked = [cell in called_numbers for cell in board]
    return sum(1 for line in LINES if all(marked[i] for i in line))
