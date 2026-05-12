from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.cli import import_godot_log
from alienhand_ai.runner import ProcessRunConfig, record_process


class RunnerCliTests(unittest.TestCase):
    def test_records_short_process_without_window_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            result = record_process(
                ProcessRunConfig(
                    command=[sys.executable, "-c", "print('hello')"],
                    output=temp,
                    name="process",
                    frames=10,
                    interval=0.05,
                    capture_window=False,
                    capture_images=False,
                    resource_metrics=False,
                    watch_title_only=True,
                    report=True,
                )
            )

            run_dir = Path(result["summary"]["run_dir"])
            self.assertTrue((run_dir / "events.jsonl").exists())
            self.assertFalse(any((run_dir / "frames").glob("*.bmp")))
            self.assertTrue((run_dir / "stdout.log").read_text(encoding="utf-8").strip().endswith("hello"))
            self.assertTrue((run_dir / "report.html").exists())
            self.assertFalse(result["summary"]["metadata"]["capture_images"])
            self.assertTrue(result["summary"]["metadata"]["watch_title_only"])

    def test_imports_godot_log_to_telemetry_run(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "godot.jsonl"
            log.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in (
                        {"type": "godot_event", "kind": "ready", "session_id": "demo", "frame_index": 0, "ticks_msec": 1, "unix_time": 1.0, "payload": {}},
                        {"type": "godot_frame", "session_id": "demo", "frame_index": 1, "ticks_msec": 2, "unix_time": 2.0, "state": {"scene": "res://main.tscn"}},
                    )
                ),
                encoding="utf-8",
            )

            result = import_godot_log(log, temp, "imported", report=True)

            self.assertEqual(result["summary"]["frames"], 1)
            self.assertEqual(result["godot"]["sessions"], ["demo"])
            self.assertTrue((Path(result["summary"]["run_dir"]) / "report.html").exists())


if __name__ == "__main__":
    unittest.main()
