from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.interaction_trace import build_interaction_transitions, summarize_interaction_trace, write_interaction_trace
from alienhand_ai.learning_packets import build_learning_packets, summarize_learning_packets, write_learning_packets


class InteractionLearningPacketTests(unittest.TestCase):
    def test_builds_previous_event_current_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = self._write_run(Path(temp))

            transitions = build_interaction_transitions(run_dir)

            self.assertEqual(len(transitions), 1)
            self.assertEqual(transitions[0].input_event["action"], "left_down")
            self.assertEqual(transitions[0].previous_frame["index"], 1)
            self.assertEqual(transitions[0].current_frame["index"], 2)
            self.assertTrue(transitions[0].effect["window_title_changed"])
            self.assertEqual(transitions[0].effect["visual_track_count_delta"], 1)

            output = write_interaction_trace(run_dir)
            self.assertTrue(output.exists())
            self.assertEqual(summarize_interaction_trace(run_dir)["transitions"], 1)

    def test_builds_pending_llm_learning_packets(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = self._write_run(Path(temp))

            packets = build_learning_packets(run_dir)

            self.assertEqual(len(packets), 1)
            self.assertEqual(packets[0].status, "pending_llm_digest")
            self.assertIn("semantic_effect_label", packets[0].digest["needs"])
            self.assertIn("delineated_screen_region", packets[0].digest["needs"])
            self.assertIn("novelty_vector", packets[0].payload)
            self.assertTrue(packets[0].payload["novelty_vector"]["should_packetize"])
            self.assertIn("human_demonstration", packets[0].tags)
            self.assertIn("novelty", packets[0].tags)

            output = write_learning_packets(run_dir)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["kind"], "ui_interaction_transition")
            self.assertEqual(summarize_learning_packets(run_dir)["packets"], 1)

    @staticmethod
    def _write_run(root: Path) -> Path:
        run_dir = root / "run"
        run_dir.mkdir()
        events = [
            {
                "type": "frame",
                "index": 1,
                "timestamp": 10.0,
                "image": "frames/frame_000001.bmp",
                "observation": {
                    "window": {"title": "Godot", "width": 800, "height": 600},
                    "visual_scene": {"tracks": [{"id": 1}]},
                },
                "variables": {"frame": 1, "elapsed_seconds": 0.1, "track_count": 1},
            },
            {
                "type": "event",
                "kind": "observed_input",
                "timestamp": 10.5,
                "frame_index": 1,
                "payload": {
                    "kind": "mouse",
                    "action": "left_down",
                    "payload": {"window_x": 100, "window_y": 200},
                },
            },
            {
                "type": "frame",
                "index": 2,
                "timestamp": 11.0,
                "image": "frames/frame_000002.bmp",
                "observation": {
                    "window": {"title": "Godot - Menu", "width": 800, "height": 600},
                    "visual_scene": {"tracks": [{"id": 1}, {"id": 2}]},
                },
                "variables": {"frame": 2, "elapsed_seconds": 0.2, "track_count": 2},
            },
        ]
        (run_dir / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        return run_dir


if __name__ == "__main__":
    unittest.main()
