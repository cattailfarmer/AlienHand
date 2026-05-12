from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.vision.dot_matrix import FIVE_BY_SEVEN_FONT, match_dot_matrix
from alienhand_ai.vision.lcd import (
    DisplayGeometry,
    ReadoutKind,
    decode_pattern,
    decode_patterns,
    geometry_note,
    numeric_text,
    token_numeric_text,
    unsupported_letters_for_geometry,
)


class LcdTests(unittest.TestCase):
    def test_decodes_digits_from_segments(self):
        readout = decode_patterns([set("abcdef"), set("bc"), set("acdfg")])

        self.assertEqual(readout.text, "015")
        self.assertEqual(readout.kind, ReadoutKind.NUMERIC)
        self.assertEqual(numeric_text(readout), "015")

    def test_decodes_dvd_no_disc_style_text(self):
        readout = decode_patterns([
            set("ceg"),
            set("abcdef"),
            set(),
            set("bcdeg"),
            set("bc"),
            set("acdfg"),
            set("bcefg"),
        ])

        self.assertEqual(readout.text, "n0 D15H")
        self.assertEqual(readout.kind, ReadoutKind.TEXT)
        self.assertIsNone(numeric_text(readout))
        self.assertEqual(tuple(token.kind for token in readout.tokens), (ReadoutKind.TEXT, ReadoutKind.TEXT))
        self.assertIn("O", readout.glyphs[1].alternatives)
        self.assertIn("D", readout.glyphs[3].alternatives)
        self.assertIn("I", readout.glyphs[4].alternatives)
        self.assertIn("S", readout.glyphs[5].alternatives)

    def test_classifies_numeric_and_text_per_word(self):
        readout = decode_patterns([
            set("abcdef"),
            set("bc"),
            set(),
            set("ceg"),
            set("abcdef"),
        ])

        self.assertEqual(readout.kind, ReadoutKind.TEXT)
        self.assertEqual(tuple(token.kind for token in readout.tokens), (ReadoutKind.NUMERIC, ReadoutKind.TEXT))
        self.assertEqual(token_numeric_text(readout.tokens[0]), "01")
        self.assertIsNone(token_numeric_text(readout.tokens[1]))

    def test_picks_nearest_known_segment_pattern(self):
        glyph = decode_pattern(frozenset("abcefg"))

        self.assertEqual(glyph.char, "A")
        self.assertGreaterEqual(glyph.confidence, 1.0)

    def test_flags_unreliable_letters_for_seven_segment_displays(self):
        self.assertEqual(unsupported_letters_for_geometry("TWO WOKS"), ("T", "W", "K"))
        self.assertEqual(unsupported_letters_for_geometry("TWO WOKS", DisplayGeometry.FOURTEEN_SEGMENT), ())
        self.assertIn("dot", geometry_note(DisplayGeometry.DOT_MATRIX).lower())

    def test_matches_classic_dot_matrix_template(self):
        match = match_dot_matrix(FIVE_BY_SEVEN_FONT["D"].rows)

        self.assertEqual(match.char, "D")
        self.assertEqual(match.distance, 0)


if __name__ == "__main__":
    unittest.main()
