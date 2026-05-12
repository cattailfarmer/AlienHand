from __future__ import annotations

from dataclasses import dataclass

from .board import Board, Cell


@dataclass(frozen=True)
class Move:
    row: int
    col: int
    action: str
    reason: str


NUMBER_VALUES = {
    Cell.N1: 1,
    Cell.N2: 2,
    Cell.N3: 3,
    Cell.N4: 4,
    Cell.N5: 5,
    Cell.N6: 6,
    Cell.N7: 7,
    Cell.N8: 8,
}


def suggest_moves(board: Board) -> list[Move]:
    moves: list[Move] = []
    seen: set[tuple[int, int, str]] = set()

    for row in range(board.rows):
        for col in range(board.cols):
            cell = board.at(row, col)
            if cell not in NUMBER_VALUES:
                continue

            value = NUMBER_VALUES[cell]
            neighbors = board.neighbors(row, col)
            unknown = [(r, c) for r, c in neighbors if board.at(r, c) == Cell.UNKNOWN]
            flags = [(r, c) for r, c in neighbors if board.at(r, c) == Cell.FLAG]

            if unknown and value == len(flags):
                for r, c in unknown:
                    _append_once(moves, seen, Move(r, c, "open", f"{cell.value} already has all mines flagged"))
            if unknown and value - len(flags) == len(unknown):
                for r, c in unknown:
                    _append_once(moves, seen, Move(r, c, "flag", f"{cell.value} remaining unknowns are mines"))

    if not moves:
        for row in range(board.rows):
            for col in range(board.cols):
                if board.at(row, col) == Cell.UNKNOWN:
                    return [Move(row, col, "open", "fallback first unknown")]
    return moves


def _append_once(moves: list[Move], seen: set[tuple[int, int, str]], move: Move) -> None:
    key = (move.row, move.col, move.action)
    if key not in seen:
        seen.add(key)
        moves.append(move)

