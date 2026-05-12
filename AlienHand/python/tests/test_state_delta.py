from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.state_delta import build_state_snapshot, diff_state, summarize_delta


class StateDeltaTests(unittest.TestCase):
    def test_reports_added_changed_and_removed_leaves(self):
        previous = build_state_snapshot(
            state={"score": 1, "lives": 3, "old": True},
            variables={"frame": 1},
        )
        current = build_state_snapshot(
            state={"score": 2, "lives": 3, "new": "value"},
            variables={"frame": 2},
        )

        delta = diff_state(previous, current).to_dict()

        self.assertEqual(delta["added"]["state"]["new"], "value")
        self.assertEqual(delta["changed"]["state"]["score"], {"from": 1, "to": 2})
        self.assertEqual(delta["changed"]["variables"]["frame"], {"from": 1, "to": 2})
        self.assertTrue(delta["removed"]["state"]["old"])
        self.assertEqual(summarize_delta(delta), {"added": 1, "changed": 2, "removed": 1})


if __name__ == "__main__":
    unittest.main()
