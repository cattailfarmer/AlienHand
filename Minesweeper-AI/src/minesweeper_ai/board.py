from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from alienhand_ai.bitmap import Bitmap


class Cell(str, Enum):
    UNKNOWN = "unknown"
    FLAG = "flag"
    MINE = "mine"
    EMPTY = "empty"
    N1 = "1"
    N2 = "2"
    N3 = "3"
    N4 = "4"
    N5 = "5"
    N6 = "6"
    N7 = "7"
    N8 = "8"


@dataclass(frozen=True)
class Board:
    rows: int
    cols: int
    cells: tuple[Cell, ...]

    def at(self, row: int, col: int) -> Cell:
        return self.cells[row * self.cols + col]

    def neighbors(self, row: int, col: int) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = row + dr, col + dc
                if 0 <= rr < self.rows and 0 <= cc < self.cols:
                    out.append((rr, cc))
        return out


@dataclass(frozen=True)
class GridSpec:
    left: int
    top: int
    cell_size: int
    rows: int
    cols: int

    def center(self, row: int, col: int) -> tuple[int, int]:
        return (
            self.left + col * self.cell_size + self.cell_size // 2,
            self.top + row * self.cell_size + self.cell_size // 2,
        )


def classify_cell(bitmap: Bitmap, x: int, y: int, size: int) -> Cell:
    samples = [
        bitmap.rgb_at(min(bitmap.width - 1, x + size // 2), min(bitmap.height - 1, y + size // 2)),
        bitmap.rgb_at(min(bitmap.width - 1, x + size // 3), min(bitmap.height - 1, y + size // 3)),
        bitmap.rgb_at(min(bitmap.width - 1, x + (size * 2) // 3), min(bitmap.height - 1, y + (size * 2) // 3)),
    ]
    avg = tuple(sum(pixel[i] for pixel in samples) // len(samples) for i in range(3))
    r, g, b = avg

    if r > 180 and g > 180 and b > 180:
        return Cell.UNKNOWN
    if b > r + 40 and b > g + 20:
        return Cell.N1
    if g > r + 35 and g > b + 20:
        return Cell.N2
    if r > g + 40 and r > b + 40:
        return Cell.N3
    if r < 90 and g < 90 and b < 130:
        return Cell.EMPTY
    return Cell.EMPTY


def read_board(bitmap: Bitmap, spec: GridSpec) -> Board:
    cells: list[Cell] = []
    for row in range(spec.rows):
        for col in range(spec.cols):
            cells.append(classify_cell(bitmap, spec.left + col * spec.cell_size, spec.top + row * spec.cell_size, spec.cell_size))
    return Board(rows=spec.rows, cols=spec.cols, cells=tuple(cells))
