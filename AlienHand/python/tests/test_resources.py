from pathlib import Path
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.resources import ResourceMonitor, sample_gpu_resources, sample_system_resources


class ResourceTests(unittest.TestCase):
    def test_samples_current_process_resources(self):
        monitor = ResourceMonitor()
        snapshot = monitor.sample(os.getpid())

        self.assertTrue(snapshot.process["available"])
        self.assertEqual(snapshot.process["pid"], os.getpid())
        self.assertIn("memory", snapshot.process)
        self.assertIn("io", snapshot.process)
        self.assertIn("memory", snapshot.system)

    def test_gpu_probe_reports_available_or_reason(self):
        gpu = sample_gpu_resources()

        self.assertIn("available", gpu)
        self.assertIn("backend", gpu)

    def test_system_resources_include_drive_when_requested(self):
        system = sample_system_resources(Path.cwd())

        self.assertIn("cpu_count", system)
        self.assertIn("drive", system)


if __name__ == "__main__":
    unittest.main()
