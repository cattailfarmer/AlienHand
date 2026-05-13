from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.thelounge_adapter import (
    build_thelounge_preview_html,
    run_thelounge_adapter_proof,
    sample_thelounge_render_rows,
    summarize_thelounge_render_rows,
)


class TheLoungeAdapterTests(unittest.TestCase):
    def test_sample_rows_match_thelounge_adapter_contract(self):
        rows = sample_thelounge_render_rows()
        summary = summarize_thelounge_render_rows(rows)

        self.assertTrue(summary["adapter_ok"])
        self.assertEqual(summary["orientations"], ["left", "right", "system"])
        self.assertIn("code", summary["frame_kinds"])
        self.assertIn("image", summary["frame_kinds"])
        self.assertIn("link", summary["frame_kinds"])
        self.assertEqual(summary["payload_error_rows"], 1)

    def test_preview_html_escapes_text_and_renders_frames(self):
        rows = sample_thelounge_render_rows()
        rows[0]["content"]["text"] = "<script>alert('no')</script>"

        html = build_thelounge_preview_html(rows)

        self.assertIn("alienhand-message--left", html)
        self.assertIn("alienhand-message--right", html)
        self.assertIn("alienhand-message--system", html)
        self.assertIn("alienhand-frame--code", html)
        self.assertIn("alienhand-frame--image", html)
        self.assertIn("alienhand-frame--link", html)
        self.assertIn("../../../thelounge-alienhand/client/css/alienhand-chat.css", html)
        self.assertIn("&lt;script&gt;alert(&#x27;no&#x27;)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert", html)

    def test_thelounge_adapter_proof_writes_fixture_and_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_thelounge_adapter_proof(Path(temp) / "thelounge-adapter")
            fixture_path = Path(result["fixture_path"])
            preview_path = Path(result["preview_path"])

            self.assertTrue(result["summary"]["adapter_ok"])
            self.assertTrue(fixture_path.exists())
            self.assertTrue(preview_path.exists())
            self.assertEqual(len(json.loads(fixture_path.read_text(encoding="utf-8"))), 3)
            self.assertIn("AH-THELOUNGE/1", preview_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
