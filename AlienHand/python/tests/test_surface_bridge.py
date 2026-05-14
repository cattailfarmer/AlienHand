from pathlib import Path
import socket
import sys
import tempfile
from time import sleep
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.surface_bridge import (
    DEFAULT_GODOT_SURFACE_ID,
    SURFACE_POINTER_EVENT,
    SURFACE_PROTOCOL,
    SurfaceEventJSONLServer,
    SurfacePointerEvent,
    godot_surface_integration_handle,
    run_godot_surface_bridge_proof,
)


class SurfaceBridgeTests(unittest.TestCase):
    def test_godot_surface_bridge_proof_streams_surface_events(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_godot_surface_bridge_proof(Path(temp))

        self.assertTrue(result["bridge_ok"])
        self.assertEqual(result["protocol"], SURFACE_PROTOCOL)
        self.assertEqual(result["surface_events"][0]["type"], SURFACE_POINTER_EVENT)
        self.assertEqual(result["surface_events"][0]["pointer_id"], "alienhand:mouse-b")
        self.assertEqual(result["integration_handle"]["transport"], "tcp-jsonl")
        cursor = result["surface_events"][0]["metadata"]["cursor"]
        self.assertEqual(cursor["cursor_presentation"], "left_hand_reflected")
        self.assertEqual(cursor["coordinate_policy"], "x_y_are_hotspot_not_mirrored")
        self.assertEqual(cursor["reflected_hotspot"]["x"], 31)

    def test_surface_server_publishes_jsonl_to_client(self):
        with SurfaceEventJSONLServer() as server:
            host, port = server.address
            with socket.create_connection((host, port), timeout=2.0) as client:
                for _attempt in range(100):
                    if server.client_count:
                        break
                    sleep(0.01)
                server.publish(_sample_surface_event())
                line = client.recv(4096).decode("utf-8").strip()

        self.assertIn(SURFACE_PROTOCOL, line)
        self.assertIn(SURFACE_POINTER_EVENT, line)

    def test_integration_handle_names_stable_godot_contract(self):
        handle = godot_surface_integration_handle("127.0.0.1", 47991, DEFAULT_GODOT_SURFACE_ID)

        self.assertEqual(handle["protocol"], SURFACE_PROTOCOL)
        self.assertEqual(handle["event_type"], SURFACE_POINTER_EVENT)
        self.assertIn("pointer_id", handle["godot_contract"]["required_fields"])
        self.assertIn("metadata", handle["godot_contract"]["required_fields"])
        self.assertEqual(handle["godot_contract"]["cursor_contract"]["presentation"], "left_hand_reflected")


def _sample_surface_event():
    return SurfacePointerEvent(
        sequence=0,
        surface_id=DEFAULT_GODOT_SURFACE_ID,
        pointer_id="alienhand:mouse-b",
        device_id="mouse-b",
        action="move",
        x=100,
        y=200,
        dx=1,
        dy=2,
        buttons=(),
        wheel_delta=0,
        timestamp_ms=123,
        source_channel="alienhand_independent",
        target_id="godot-alienhand-surface",
        metadata={"test": True},
    )


if __name__ == "__main__":
    unittest.main()
