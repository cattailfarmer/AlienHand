from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.learning_memory import ingest_learning_digest, learned_edge_from_digest, summarize_learning_memory


class LearningMemoryTests(unittest.TestCase):
    def test_turns_llm_digest_into_learned_novelty_edge(self):
        packet = {
            "id": "learn_123",
            "kind": "ui_interaction_transition",
            "payload": {
                "novelty_vector": {
                    "novelty_score": 0.88,
                    "triggers": ["recognition_below_threshold"],
                }
            },
            "digest": {},
        }
        digest = {
            "packet_id": "learn_123",
            "semantic_effect_label": "run_project",
            "confidence": 0.91,
            "mapped_interface_route": "godot.api.EditorInterface.play_main_scene",
            "sop_text": "call godot.api.EditorInterface.play_main_scene",
        }

        edge = learned_edge_from_digest(packet, digest)

        self.assertEqual(edge.packet_id, "learn_123")
        self.assertEqual(edge.semantic_label, "run_project")
        self.assertEqual(edge.interface_route, "godot.api.EditorInterface.play_main_scene")
        self.assertEqual(edge.sop["summary"]["by_status"]["executable"], 1)

    def test_ingests_digest_into_memory_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packets = root / "packets.jsonl"
            digest = root / "digest.json"
            output = root / "memory.json"
            packets.write_text(
                json.dumps(
                    {
                        "id": "sop_123",
                        "kind": "sop_distillation",
                        "payload": {"step": {"source": "wait until it looks stable"}},
                        "digest": {"novelty_vector": {"novelty_score": 0.5}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            digest.write_text(
                json.dumps(
                    {
                        "packet_id": "sop_123",
                        "semantic_effect_label": "wait_for_stable_editor",
                        "confidence": 0.84,
                        "sop_text": "wait until frame_delta_below 0.02 for 3 frames",
                    }
                ),
                encoding="utf-8",
            )

            memory = ingest_learning_digest(packets, digest, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(summarize_learning_memory(memory)["entries"], 1)
        self.assertEqual(payload["entries"][0]["semantic_label"], "wait_for_stable_editor")


if __name__ == "__main__":
    unittest.main()
