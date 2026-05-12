from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.input_observer import InputObserverConfig, _signed_high_word, matches_target_window
from alienhand_ai.window_capture import WindowInfo


class InputObserverTests(unittest.TestCase):
    def test_matches_target_window_filters_process_hwnd_and_title(self):
        info = WindowInfo(hwnd=100, title="Godot Editor", process_id=42, left=0, top=0, right=800, bottom=600)

        self.assertTrue(matches_target_window(info, InputObserverConfig(process_id=42, title_contains="godot")))
        self.assertFalse(matches_target_window(info, InputObserverConfig(process_id=43)))
        self.assertFalse(matches_target_window(info, InputObserverConfig(hwnd=200)))
        self.assertFalse(matches_target_window(info, InputObserverConfig(title_contains="notepad")))

    def test_signed_high_word_decodes_mouse_wheel_delta(self):
        self.assertEqual(_signed_high_word(0x00780000), 120)
        self.assertEqual(_signed_high_word(0xFF880000), -120)


if __name__ == "__main__":
    unittest.main()
