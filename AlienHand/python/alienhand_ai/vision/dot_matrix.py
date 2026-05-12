from __future__ import annotations

from dataclasses import dataclass


MatrixRows = tuple[str, ...]


@dataclass(frozen=True)
class DotMatrixGlyph:
    char: str
    rows: MatrixRows

    @property
    def width(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    @property
    def height(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class DotMatrixMatch:
    char: str
    confidence: float
    distance: int


FIVE_BY_SEVEN_FONT: dict[str, DotMatrixGlyph] = {
    "0": DotMatrixGlyph("0", ("01110", "10001", "10011", "10101", "11001", "10001", "01110")),
    "1": DotMatrixGlyph("1", ("00100", "01100", "00100", "00100", "00100", "00100", "01110")),
    "2": DotMatrixGlyph("2", ("01110", "10001", "00001", "00010", "00100", "01000", "11111")),
    "3": DotMatrixGlyph("3", ("11110", "00001", "00001", "01110", "00001", "00001", "11110")),
    "4": DotMatrixGlyph("4", ("00010", "00110", "01010", "10010", "11111", "00010", "00010")),
    "5": DotMatrixGlyph("5", ("11111", "10000", "10000", "11110", "00001", "00001", "11110")),
    "6": DotMatrixGlyph("6", ("01110", "10000", "10000", "11110", "10001", "10001", "01110")),
    "7": DotMatrixGlyph("7", ("11111", "00001", "00010", "00100", "01000", "01000", "01000")),
    "8": DotMatrixGlyph("8", ("01110", "10001", "10001", "01110", "10001", "10001", "01110")),
    "9": DotMatrixGlyph("9", ("01110", "10001", "10001", "01111", "00001", "00001", "01110")),
    "A": DotMatrixGlyph("A", ("01110", "10001", "10001", "11111", "10001", "10001", "10001")),
    "D": DotMatrixGlyph("D", ("11110", "10001", "10001", "10001", "10001", "10001", "11110")),
    "H": DotMatrixGlyph("H", ("10001", "10001", "10001", "11111", "10001", "10001", "10001")),
    "I": DotMatrixGlyph("I", ("01110", "00100", "00100", "00100", "00100", "00100", "01110")),
    "N": DotMatrixGlyph("N", ("10001", "11001", "10101", "10011", "10001", "10001", "10001")),
    "O": DotMatrixGlyph("O", ("01110", "10001", "10001", "10001", "10001", "10001", "01110")),
    "S": DotMatrixGlyph("S", ("01111", "10000", "10000", "01110", "00001", "00001", "11110")),
    "Y": DotMatrixGlyph("Y", ("10001", "10001", "01010", "00100", "00100", "00100", "00100")),
}


def match_dot_matrix(rows: MatrixRows, font: dict[str, DotMatrixGlyph] | None = None) -> DotMatrixMatch:
    font = font or FIVE_BY_SEVEN_FONT
    normalized = _normalize_rows(rows)
    best_char = "?"
    best_distance = 10**9
    best_size = 1
    for char, glyph in font.items():
        candidate = _normalize_rows(glyph.rows)
        distance = _hamming_distance(normalized, candidate)
        size = max(_cell_count(normalized), _cell_count(candidate), 1)
        if distance < best_distance:
            best_char = char
            best_distance = distance
            best_size = size
    return DotMatrixMatch(char=best_char, confidence=1.0 - (best_distance / best_size), distance=best_distance)


def _normalize_rows(rows: MatrixRows) -> MatrixRows:
    width = max((len(row) for row in rows), default=0)
    return tuple(row.ljust(width, "0") for row in rows)


def _hamming_distance(left: MatrixRows, right: MatrixRows) -> int:
    height = max(len(left), len(right))
    width = max((len(row) for row in left + right), default=0)
    distance = 0
    for y in range(height):
        left_row = left[y] if y < len(left) else ""
        right_row = right[y] if y < len(right) else ""
        for x in range(width):
            left_cell = left_row[x] if x < len(left_row) else "0"
            right_cell = right_row[x] if x < len(right_row) else "0"
            if left_cell != right_cell:
                distance += 1
    return distance


def _cell_count(rows: MatrixRows) -> int:
    return sum(len(row) for row in rows)
