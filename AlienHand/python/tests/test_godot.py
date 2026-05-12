from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.godot import load_godot_events, normalize_godot_event, summarize_godot_events


class GodotProtocolTests(unittest.TestCase):
    def test_normalizes_frame_state_payload(self):
        event = normalize_godot_event(
            {
                "type": "godot_frame",
                "session_id": "demo",
                "frame_index": 3,
                "ticks_msec": 42,
                "unix_time": 100.5,
                "state": {"scene": "res://main.tscn"},
            }
        )

        self.assertEqual(event.payload["scene"], "res://main.tscn")
        self.assertEqual(event.frame_index, 3)

    def test_loads_jsonl_and_summarizes(self):
        rows = [
            {"type": "godot_event", "kind": "start", "session_id": "demo", "frame_index": 0, "ticks_msec": 1, "unix_time": 1.0, "payload": {}},
            {"type": "godot_frame", "session_id": "demo", "frame_index": 1, "ticks_msec": 2, "unix_time": 2.0, "state": {"score": 10}},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "godot.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            events = load_godot_events(path)
            summary = summarize_godot_events(events)

        self.assertEqual(summary["frames"], 1)
        self.assertEqual(summary["sessions"], ["demo"])


if __name__ == "__main__":
    unittest.main()
