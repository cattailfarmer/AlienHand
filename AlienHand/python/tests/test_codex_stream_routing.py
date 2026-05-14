import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.codex_stream_routing import (
    MODEL_HIGH,
    MODEL_SPARK,
    route_codex_stream_request,
)


class CodexStreamRoutingTests(unittest.TestCase):
    def test_route_prefers_spark_for_known_spark_tasks(self):
        route = route_codex_stream_request(task_type="health_check")
        self.assertEqual(route.selected_model, MODEL_SPARK)
        self.assertEqual(route.budget_class, "spark_suitable")

    def test_route_prefers_high_for_high_reasoning_tasks(self):
        route = route_codex_stream_request(task_type="sop_compile")
        self.assertEqual(route.selected_model, MODEL_HIGH)
        self.assertEqual(route.budget_class, "high_reasoning")

    def test_route_respects_explicit_model_hint(self):
        route = route_codex_stream_request(task_type="health_check", model_budget_hint="explicit_model:custom/model")
        self.assertEqual(route.selected_model, "custom/model")
        self.assertEqual(route.budget_class, "explicit_model")

    def test_route_default_falls_back_to_high_for_unknown_task(self):
        route = route_codex_stream_request(task_type="mystery_task")
        self.assertEqual(route.selected_model, MODEL_HIGH)
        self.assertEqual(route.budget_class, "high_reasoning")


if __name__ == "__main__":
    unittest.main()
