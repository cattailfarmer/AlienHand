from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.learning import get_learning_profile, list_learning_profiles
from alienhand_ai.telemetry import DecisionStep, DecisionTrace, TelemetryRecorder, load_events, next_numeric_run_name


class TelemetryTests(unittest.TestCase):
    def test_records_events_frames_and_report(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "frame.bmp"
            image.write_bytes(b"BMfake")
            recorder = TelemetryRecorder(temp, "sample", metadata={"game": "test"})
            recorder.log_event("start", {"ok": True})
            frame = recorder.record_frame(
                image_path=image,
                observation={"cells": 81},
                variables={"score": 10},
                resources={"process": {"pid": 123}},
                decision=DecisionTrace(
                    policy="rule",
                    steps=(DecisionStep(label="scan", output={"unknown": 4}, confidence=0.75),),
                    selected_action={"type": "open", "row": 1, "col": 2},
                ),
                action={"type": "open"},
                debug={"note": "unit test"},
            )
            report = recorder.write_report()
            events = load_events(recorder.events_path)

            self.assertEqual(frame.index, 1)
            self.assertEqual(frame.resources["process"]["pid"], 123)
            self.assertEqual(frame.state, {})
            self.assertIn("summary", frame.delta)
            self.assertEqual(len(events), 2)
            self.assertIn("delta", events[1])
            self.assertTrue((recorder.run_dir / "frames" / "frame_000001.bmp").exists())
            self.assertTrue(report.exists())
            self.assertEqual(recorder.summary()["frames"], 1)

    def test_records_state_deltas_without_images(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder = TelemetryRecorder(temp, "state")
            first = recorder.record_frame(state={"score": 0, "menu": "title"})
            second = recorder.record_frame(state={"score": 10, "menu": "playing"})
            events = load_events(recorder.events_path)

            self.assertIsNone(first.image)
            self.assertEqual(first.delta["summary"]["added"], 2)
            self.assertEqual(second.delta["changed"]["state"]["score"], {"from": 0, "to": 10})
            self.assertEqual(second.delta["changed"]["state"]["menu"], {"from": "title", "to": "playing"})
            self.assertFalse((recorder.run_dir / "frames" / "frame_000001.bmp").exists())
            self.assertEqual(events[-1]["state"]["score"], 10)

    def test_auto_run_names_increment_from_numeric_folders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "1").mkdir()
            (root / "3").mkdir()
            (root / "named").mkdir()
            (root / "2.txt").write_text("not a run", encoding="utf-8")

            self.assertEqual(next_numeric_run_name(root), "4")
            recorder = TelemetryRecorder(root)

            self.assertEqual(recorder.run_name, "4")
            self.assertEqual(recorder.run_dir.name, "4")
            self.assertEqual(next_numeric_run_name(root), "5")

    def test_explicit_run_name_overrides_auto_numbering(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder = TelemetryRecorder(temp, "space-test")

            self.assertEqual(recorder.run_name, "space-test")
            self.assertEqual(recorder.run_dir.name, "space-test")

    def test_learning_profiles_are_named_presets(self):
        steady = get_learning_profile("steady")
        names = {profile.name for profile in list_learning_profiles()}

        self.assertGreater(steady.backpropagation_rate, 0.0)
        self.assertIn("novice", names)
        self.assertIn("expert", names)


if __name__ == "__main__":
    unittest.main()
