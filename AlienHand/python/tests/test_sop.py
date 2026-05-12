from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.sop import compile_sop_text, write_sop_packets


class SOPTests(unittest.TestCase):
    def test_compiles_clear_steps_to_executable_primitives(self):
        program = compile_sop_text(
            """
            1. observe project window
            2. click Run Project button
            3. press F5
            4. call godot.api.EditorInterface.play_main_scene
            """,
            name="godot-run",
        )

        self.assertEqual(len(program.steps), 4)
        self.assertEqual([step.primitive for step in program.steps], ["observe", "click", "key", "interface_route"])
        self.assertTrue(all(step.status == "executable" for step in program.steps))
        self.assertEqual(program.packets, ())

    def test_subjective_language_becomes_pending_digest_packet(self):
        program = compile_sop_text(
            """
            wait until the editor looks stable
            if the result seems correct then save scene
            """,
            name="subjective-godot",
        )

        self.assertEqual(len(program.steps), 2)
        self.assertTrue(all(step.status == "needs_digest" for step in program.steps))
        self.assertEqual(len(program.packets), 2)
        self.assertEqual(program.packets[0].kind, "sop_distillation")
        self.assertEqual(program.packets[0].status, "pending_llm_digest")
        self.assertIn("subjective_term_resolution", program.packets[0].tags)
        self.assertIn("threshold_binding", program.packets[0].digest["needs"])

    def test_writes_program_and_packets(self):
        with tempfile.TemporaryDirectory() as temp:
            program = compile_sop_text("wait until it looks right", name="demo")
            program_path = program.write_json(Path(temp) / "sop.json")
            packet_path = write_sop_packets(program, Path(temp) / "sop_packets.jsonl")

            program_payload = json.loads(program_path.read_text(encoding="utf-8"))
            packets = [json.loads(line) for line in packet_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(program_payload["schema_version"], 1)
        self.assertEqual(program_payload["summary"]["packets"], 1)
        self.assertEqual(packets[0]["status"], "pending_llm_digest")


if __name__ == "__main__":
    unittest.main()
