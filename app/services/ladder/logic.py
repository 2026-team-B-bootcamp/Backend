import random

DEFAULT_ROWS = 10


def generate_rungs(columns: int, rows: int = DEFAULT_ROWS, rng: random.Random | None = None) -> list[list[bool]]:
    """columns개의 세로줄 사이에 rows개의 가로줄(사다리 칸)을 무작위로 놓는다.

    같은 줄에서 인접한 두 칸이 동시에 이어지면 경로가 애매해지므로, 하나를 놓으면
    바로 옆 칸은 건너뛴다.
    """
    rng = rng or random.Random()
    rungs: list[list[bool]] = []
    for _ in range(rows):
        row = [False] * (columns - 1)
        col = 0
        while col < columns - 1:
            if rng.random() < 0.35:
                row[col] = True
                col += 2
            else:
                col += 1
        rungs.append(row)
    return rungs


def trace(rungs: list[list[bool]], start: int, columns: int) -> int:
    """start번 세로줄 맨 위에서 출발해 가로줄을 따라 이동한 뒤 도착하는 줄 번호."""
    pos = start
    for row in rungs:
        if pos > 0 and row[pos - 1]:
            pos -= 1
        elif pos < columns - 1 and row[pos]:
            pos += 1
    return pos


def compute_assignment(rungs: list[list[bool]], columns: int) -> list[int]:
    """assignment[i] = i번 참가자가 도착하는 결과 인덱스."""
    return [trace(rungs, i, columns) for i in range(columns)]
