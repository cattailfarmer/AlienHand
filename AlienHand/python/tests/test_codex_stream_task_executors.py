import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.codex_stream_task_executors import execute_stubbed_task


class CodexStreamTaskExecutorsTests(unittest.TestCase):
    def test_health_check_stub_reports_complete(self):
        result = execute_stubbed_task({"task_type": "health_check"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result_summary"], "health check stub executed")

    def test_sop_compile_stub_reports_needs_human(self):
        result = execute_stubbed_task({"task_type": "sop_compile"})
        self.assertEqual(result["status"], "needs_human")
        self.assertIn("SOP compile", result["result_summary"])
        self.assertEqual(result["error_ref"], "stubbed_task:sop_compile")

    def test_unknown_task_stub_requests_human(self):
        result = execute_stubbed_task({"task_type": "mystery_task"})
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["error_ref"], "stubbed_task:unsupported")

    def test_tool_handoff_has_error_reference(self):
        result = execute_stubbed_task({"task_type": "tool_handoff"})
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["error_ref"], "stubbed_task:tool_handoff")


if __name__ == "__main__":
    unittest.main()
