from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.interaction_trace import InteractionTransition
from alienhand_ai.novelty import NoveltyGateConfig, compute_novelty_vector


class NoveltyTests(unittest.TestCase):
    def test_low_confidence_transition_routes_to_learning_packet(self):
        transition = InteractionTransition(
            input_event={"kind": "mouse", "action": "left_down", "payload": {"window_x": 10, "window_y": 20}},
            previous_frame={"image": "before.bmp", "window": {"title": "Godot"}, "visual_track_count": 1},
            current_frame={"image": "after.bmp", "window": {"title": "Godot - Menu"}, "visual_track_count": 3},
            effect={
                "paired": True,
                "window_title_changed": True,
                "window_size_changed": False,
                "image_changed": True,
                "visual_track_count_delta": 2,
            },
        )

        novelty = compute_novelty_vector(transition)

        self.assertTrue(novelty.should_packetize)
        self.assertEqual(novelty.recorder_action, "continue_recording_and_queue_packet")
        self.assertIn("semantic_labeling", novelty.protocols)
        self.assertIn("interface_route_matching", novelty.protocols)

    def test_known_transition_can_record_summary_only(self):
        transition = InteractionTransition(
            input_event={"kind": "keyboard", "action": "down", "payload": {}},
            previous_frame={"image": "before.bmp", "window": {"title": "Godot"}, "visual_track_count": 1},
            current_frame={"image": "after.bmp", "window": {"title": "Godot"}, "visual_track_count": 1},
            effect={
                "paired": True,
                "window_title_changed": False,
                "window_size_changed": False,
                "image_changed": False,
                "visual_track_count_delta": 0,
            },
        )

        novelty = compute_novelty_vector(
            transition,
            config=NoveltyGateConfig(novelty_threshold=0.35),
            recognition_confidence=0.95,
            identity_confidence=0.95,
            delineation_confidence=0.95,
            mapping_confidence=0.90,
        )

        self.assertFalse(novelty.should_packetize)
        self.assertEqual(novelty.recorder_action, "record_summary_only")
        self.assertEqual(novelty.triggers, ())


if __name__ == "__main__":
    unittest.main()
