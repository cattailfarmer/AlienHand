from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.bitmap import Bitmap
from alienhand_ai.vision.tracking import ObjectTracker, detect_foreground, detect_motion, scene_feature_matrix, summarize_track_group_motion
from alienhand_ai.vision.visual_reflex import choose_visual_reflex
from alienhand_ai.vision.visual_transformer import build_object_sequence_model
from alienhand_ai.transformer import MissingTorchError


def bitmap_from_points(width: int, height: int, points: set[tuple[int, int]]) -> Bitmap:
    pixels = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            if (x, y) in points:
                pixels[offset : offset + 3] = bytes((255, 32, 32))
            else:
                pixels[offset : offset + 3] = bytes((0, 0, 0))
    return Bitmap(width=width, height=height, pixels=bytes(pixels))


class TrackingTests(unittest.TestCase):
    def test_detects_foreground_components(self):
        bitmap = bitmap_from_points(8, 8, {(2, 2), (3, 2), (2, 3), (3, 3)})

        detections = detect_foreground(bitmap, threshold=32, min_area=4)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].bounds.to_dict(), {"left": 2, "top": 2, "right": 4, "bottom": 4})

    def test_tracks_object_id_and_velocity(self):
        first = bitmap_from_points(10, 8, {(2, 2), (3, 2), (2, 3), (3, 3)})
        second = bitmap_from_points(10, 8, {(4, 2), (5, 2), (4, 3), (5, 3)})
        tracker = ObjectTracker(max_distance=5)

        scene_a = tracker.update(detect_foreground(first, threshold=32, min_area=4))
        scene_b = tracker.update(detect_foreground(second, threshold=32, min_area=4))

        self.assertEqual(scene_a.tracks[0].id, scene_b.tracks[0].id)
        self.assertEqual(scene_b.tracks[0].velocity, (2.0, 0.0))

    def test_motion_detection_finds_changed_regions(self):
        first = bitmap_from_points(8, 8, {(1, 1), (1, 2)})
        second = bitmap_from_points(8, 8, {(5, 1), (5, 2)})

        detections = detect_motion(first, second, threshold=40, min_area=2)

        self.assertEqual(len(detections), 2)

    def test_visual_reflex_selects_moving_track(self):
        first = bitmap_from_points(10, 8, {(2, 2), (3, 2), (2, 3), (3, 3)})
        second = bitmap_from_points(10, 8, {(6, 2), (7, 2), (6, 3), (7, 3)})
        tracker = ObjectTracker(max_distance=8)
        tracker.update(detect_foreground(first, threshold=32, min_area=4))
        scene = tracker.update(detect_foreground(second, threshold=32, min_area=4))

        reflex = choose_visual_reflex(scene.tracks)
        features = scene_feature_matrix(scene.tracks, 10, 8, max_tracks=2)

        self.assertEqual(reflex.focus_track_id, scene.tracks[0].id)
        self.assertEqual(len(features), 2)
        self.assertEqual(len(features[0]), 8)

    def test_summarizes_track_group_motion(self):
        tracker = ObjectTracker(max_distance=8)
        tracker.update(detect_foreground(bitmap_from_points(16, 8, {(1, 1), (2, 1), (8, 1), (9, 1)}), threshold=32, min_area=2))
        scene = tracker.update(detect_foreground(bitmap_from_points(16, 8, {(3, 1), (4, 1), (10, 1), (11, 1)}), threshold=32, min_area=2))

        group = summarize_track_group_motion(scene.tracks)

        self.assertEqual(group["count"], 2)
        self.assertEqual(group["mean_velocity"], {"x": 2.0, "y": 0.0})
        self.assertEqual(len(group["members"]), 2)

    def test_visual_transformer_scaffold_builds_or_reports_missing_torch(self):
        try:
            model = build_object_sequence_model(max_objects=2, feature_size=8, action_types=4)
        except MissingTorchError:
            return

        self.assertEqual(type(model).__name__, "ObjectSequencePolicy")


if __name__ == "__main__":
    unittest.main()
