from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.motion import MotionCompressionConfig, compress_input_events


def mouse_move(timestamp: float, x: int, y: int) -> dict[str, object]:
    return {
        "kind": "mouse",
        "action": "move",
        "timestamp": timestamp,
        "hwnd": 1,
        "title": "Game",
        "process_id": 10,
        "payload": {
            "screen_x": x,
            "screen_y": y,
            "window_x": x,
            "window_y": y,
        },
    }


class MotionCompressionTests(unittest.TestCase):
    def test_compresses_even_mouse_motion_into_segment(self):
        events = [mouse_move(1.00 + index * 0.01, index * 10, 20) for index in range(5)]

        compressed = compress_input_events(events, MotionCompressionConfig(window_ms=100, tolerance_ratio=0.2))

        self.assertEqual(len(compressed), 1)
        self.assertEqual(compressed[0]["action"], "move_segment")
        payload = compressed[0]["payload"]
        self.assertEqual(payload["sample_count"], 5)
        self.assertTrue(payload["continuous"])
        self.assertEqual(payload["total_distance_px"], 40.0)

    def test_keeps_irregular_mouse_motion_as_raw_samples(self):
        events = [
            mouse_move(1.00, 0, 0),
            mouse_move(1.01, 10, 0),
            mouse_move(1.02, 11, 9),
            mouse_move(1.03, 35, 9),
        ]

        compressed = compress_input_events(events, MotionCompressionConfig(window_ms=100, tolerance_ratio=0.2))

        self.assertEqual([event["action"] for event in compressed], ["move", "move", "move", "move"])

    def test_preserves_buttons_between_motion_segments(self):
        click = {
            "kind": "mouse",
            "action": "left_down",
            "timestamp": 2.0,
            "hwnd": 1,
            "title": "Game",
            "process_id": 10,
            "payload": {},
        }
        events = [
            mouse_move(1.00, 0, 0),
            mouse_move(1.01, 10, 0),
            mouse_move(1.02, 20, 0),
            click,
            mouse_move(2.01, 20, 0),
            mouse_move(2.02, 20, 10),
            mouse_move(2.03, 20, 20),
        ]

        compressed = compress_input_events(events, MotionCompressionConfig(window_ms=100, tolerance_ratio=0.2))

        self.assertEqual([event["action"] for event in compressed], ["move_segment", "left_down", "move_segment"])


if __name__ == "__main__":
    unittest.main()
