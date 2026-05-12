import unittest

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "AlienHand" / "python"))

from minesweeper_ai.board import Board, Cell
from minesweeper_ai.solver import suggest_moves


class SolverTests(unittest.TestCase):
    def test_flags_all_remaining_unknowns_when_number_requires_it(self):
        board = Board(
            rows=2,
            cols=2,
            cells=(
                Cell.N1,
                Cell.UNKNOWN,
                Cell.EMPTY,
                Cell.EMPTY,
            ),
        )

        moves = suggest_moves(board)

        self.assertEqual(moves[0].action, "flag")
        self.assertEqual((moves[0].row, moves[0].col), (0, 1))

    def test_opens_unknowns_when_flags_satisfy_number(self):
        board = Board(
            rows=2,
            cols=2,
            cells=(
                Cell.N1,
                Cell.FLAG,
                Cell.UNKNOWN,
                Cell.EMPTY,
            ),
        )

        moves = suggest_moves(board)

        self.assertEqual(moves[0].action, "open")
        self.assertEqual((moves[0].row, moves[0].col), (1, 0))


if __name__ == "__main__":
    unittest.main()
