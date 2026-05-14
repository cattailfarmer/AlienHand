from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import socket
from threading import Event, Lock, Thread
from time import sleep
from typing import Any, Iterable

from .multi_mouse import (
    CHANNEL_ALIENHAND,
    DeviceAssignmentTable,
    MouseDeviceAssignment,
    MultiMouseEventJournal,
    PointerDeltaPacket,
    PointerInputEvent,
    ROLE_ALIENHAND_POINTER,
    ROLE_WINDOWS_POINTER,
    ScreenBounds,
    TargetIdentity,
    TargetPointerPolicy,
    TargetPolicyRule,
    TargetPolicyTable,
    VirtualPointerRouter,
    VirtualPointerTracker,
    WindowsRawMouseObserver,
    enumerate_windows_raw_input_devices,
    suggest_mouse_device_assignments,
)


JsonDict = dict[str, Any]

SURFACE_PROTOCOL = "AH_SURFACE/1"
SURFACE_POINTER_EVENT = "alienhand.pointer"
DEFAULT_GODOT_SURFACE_ID = "godot-main-viewport"
DEFAULT_GODOT_BRIDGE_PORT = 47991


@dataclass(frozen=True)
class SurfacePointerEvent:
    sequence: int
    surface_id: str
    pointer_id: str
    device_id: str
    action: str
    x: int
    y: int
    dx: int
    dy: int
    buttons: tuple[str, ...]
    wheel_delta: int
    timestamp_ms: int
    source_channel: str
    target_id: str
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "protocol": SURFACE_PROTOCOL,
            "type": SURFACE_POINTER_EVENT,
            "sequence": self.sequence,
            "surface_id": self.surface_id,
            "pointer_id": self.pointer_id,
            "device_id": self.device_id,
            "action": self.action,
            "x": self.x,
            "y": self.y,
            "dx": self.dx,
            "dy": self.dy,
            "buttons": list(self.buttons),
            "wheel_delta": self.wheel_delta,
            "timestamp_ms": self.timestamp_ms,
            "source_channel": self.source_channel,
            "target_id": self.target_id,
            "metadata": self.metadata,
        }


class SurfaceEventJSONLServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: socket.socket | None = None
        self._clients: list[socket.socket] = []
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            return self.host, self.port
        bound_host, bound_port = self._server.getsockname()
        return str(bound_host), int(bound_port)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def start(self) -> None:
        if self._server is not None:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen()
        server.settimeout(0.1)
        self._server = server
        self._stop.clear()
        self._thread = Thread(target=self._accept_loop, name="AlienHandSurfaceBridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def publish(self, event: SurfacePointerEvent) -> int:
        line = json.dumps(event.to_dict(), sort_keys=True).encode("utf-8") + b"\n"
        with self._lock:
            clients = list(self._clients)
        delivered = 0
        failed: list[socket.socket] = []
        for client in clients:
            try:
                client.sendall(line)
                delivered += 1
            except OSError:
                failed.append(client)
        if failed:
            with self._lock:
                self._clients = [client for client in self._clients if client not in failed]
        return delivered

    def publish_many(self, events: Iterable[SurfacePointerEvent]) -> int:
        delivered = 0
        for event in events:
            delivered += self.publish(event)
        return delivered

    def __enter__(self) -> SurfaceEventJSONLServer:
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.stop()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                client, _address = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            client.settimeout(1.0)
            with self._lock:
                self._clients.append(client)


def surface_event_from_routed(
    packet: PointerDeltaPacket,
    routed: Any,
    *,
    sequence: int,
    surface_id: str,
) -> SurfacePointerEvent | None:
    if routed.channel != CHANNEL_ALIENHAND:
        return None
    virtual_state = routed.details.get("virtual_state", {})
    buttons = tuple(str(button) for button in virtual_state.get("pressed_buttons", []))
    return SurfacePointerEvent(
        sequence=sequence,
        surface_id=surface_id,
        pointer_id=routed.pointer_id,
        device_id=routed.source_device_id,
        action=routed.action,
        x=routed.x,
        y=routed.y,
        dx=packet.dx,
        dy=packet.dy,
        buttons=buttons,
        wheel_delta=packet.wheel_delta,
        timestamp_ms=routed.timestamp_ms,
        source_channel=routed.channel,
        target_id=routed.target_id,
        metadata={"target": "godot_surface"},
    )


def godot_surface_integration_handle(host: str, port: int, surface_id: str) -> JsonDict:
    return {
        "name": "AlienHand Godot Surface Bridge",
        "protocol": SURFACE_PROTOCOL,
        "transport": "tcp-jsonl",
        "host": host,
        "port": port,
        "surface_id": surface_id,
        "event_type": SURFACE_POINTER_EVENT,
        "godot_contract": {
            "connect": f"{host}:{port}",
            "read": "newline-delimited UTF-8 JSON objects",
            "required_fields": [
                "protocol",
                "type",
                "sequence",
                "surface_id",
                "pointer_id",
                "device_id",
                "action",
                "x",
                "y",
                "dx",
                "dy",
                "buttons",
                "wheel_delta",
                "timestamp_ms",
            ],
            "actions": [
                "move",
                "left_down",
                "left_up",
                "right_down",
                "right_up",
                "middle_down",
                "middle_up",
                "wheel",
                "horizontal_wheel",
            ],
        },
    }


def run_godot_surface_bridge_proof(root: str | Path) -> JsonDict:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    packets, normalized_events, routed_events, surface_events = _simulated_godot_surface_events()
    journal = MultiMouseEventJournal(root_path / "godot-surface-bridge.jsonl")
    for event in surface_events:
        journal.append("surface_pointer_event", event.to_dict())

    received: list[JsonDict] = []
    with SurfaceEventJSONLServer() as server:
        host, port = server.address
        with socket.create_connection((host, port), timeout=2.0) as client:
            _wait_for_client(server)
            server.publish_many(surface_events)
            received = _read_jsonl_messages(client, len(surface_events))

    bridge_ok = (
        len(packets) == 4
        and len(normalized_events) == 4
        and len(routed_events) == 4
        and len(surface_events) == 4
        and len(received) == 4
        and all(message.get("protocol") == SURFACE_PROTOCOL for message in received)
        and received[0].get("pointer_id") == "alienhand:mouse-b"
    )
    output = root_path / "godot-surface-bridge-proof.json"
    result = {
        "root": str(root_path.resolve()),
        "protocol": SURFACE_PROTOCOL,
        "transport": "tcp-jsonl",
        "surface_id": DEFAULT_GODOT_SURFACE_ID,
        "packets": [packet.to_dict() for packet in packets],
        "normalized_events": [event.to_dict() for event in normalized_events],
        "routed_events": [event.to_dict() for event in routed_events],
        "surface_events": [event.to_dict() for event in surface_events],
        "received_events": received,
        "journal": {"path": str(journal.path.resolve()), "records": len(journal.read_all())},
        "integration_handle": godot_surface_integration_handle("127.0.0.1", DEFAULT_GODOT_BRIDGE_PORT, DEFAULT_GODOT_SURFACE_ID),
        "bridge_ok": bridge_ok,
    }
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["output"] = str(output.resolve())
    return result


def run_godot_surface_bridge_live(
    root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_GODOT_BRIDGE_PORT,
    surface_id: str = DEFAULT_GODOT_SURFACE_ID,
    duration_seconds: float = 30.0,
    max_events: int = 256,
) -> JsonDict:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    devices = enumerate_windows_raw_input_devices()
    assignments = suggest_mouse_device_assignments(devices)
    target = _godot_target(surface_id)
    tracker = VirtualPointerTracker(_default_bounds(), initial_positions=_initial_positions(assignments))
    router = VirtualPointerRouter(captured_device_ids=assignments.captured_device_ids(), policy_table=_godot_policy_table(surface_id))
    journal = MultiMouseEventJournal(root_path / "godot-surface-bridge-live.jsonl")
    published_events: list[SurfacePointerEvent] = []
    sequence = 0

    with SurfaceEventJSONLServer(host=host, port=port) as server:
        bound_host, bound_port = server.address

        def on_packet(packet: PointerDeltaPacket) -> None:
            nonlocal sequence
            event = tracker.normalize(packet, assignments)
            if event is None:
                return
            routed = router.route(event)
            surface_event = surface_event_from_routed(packet, routed, sequence=sequence, surface_id=surface_id)
            if surface_event is None:
                return
            sequence += 1
            published_events.append(surface_event)
            journal.append("surface_pointer_event", surface_event.to_dict())
            server.publish(surface_event)

        observer = WindowsRawMouseObserver(target)
        packets = observer.collect(duration_seconds=duration_seconds, max_events=max_events, on_packet=on_packet)

    result = {
        "root": str(root_path.resolve()),
        "protocol": SURFACE_PROTOCOL,
        "transport": "tcp-jsonl",
        "host": bound_host,
        "port": bound_port,
        "surface_id": surface_id,
        "duration_seconds": duration_seconds,
        "max_events": max_events,
        "mouse_device_count": len([device for device in devices if device.kind == "mouse"]),
        "assignment_table": assignments.to_dict(),
        "packets_observed": len(packets),
        "surface_events_published": len(published_events),
        "journal": {"path": str(journal.path.resolve()), "records": len(journal.read_all())},
        "integration_handle": godot_surface_integration_handle(bound_host, bound_port, surface_id),
        "bridge_live_ok": True,
        "bridge_note": "Live bridge observes Raw Input and publishes AlienHand surface events; it does not block, capture, or inject input.",
    }
    output = root_path / "godot-surface-bridge-live.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["output"] = str(output.resolve())
    return result


def _simulated_godot_surface_events() -> tuple[
    list[PointerDeltaPacket],
    list[PointerInputEvent],
    list[Any],
    list[SurfacePointerEvent],
]:
    target = _godot_target(DEFAULT_GODOT_SURFACE_ID)
    assignments = DeviceAssignmentTable(
        [
            MouseDeviceAssignment("raw-mouse-a", "mouse-a", ROLE_WINDOWS_POINTER),
            MouseDeviceAssignment("raw-mouse-b", "mouse-b", ROLE_ALIENHAND_POINTER),
        ]
    )
    tracker = VirtualPointerTracker(
        ScreenBounds(left=0, top=0, right=1280, bottom=720),
        initial_positions={"mouse-a": (20, 20), "mouse-b": (640, 360)},
    )
    router = VirtualPointerRouter(captured_device_ids=assignments.captured_device_ids(), policy_table=_godot_policy_table(DEFAULT_GODOT_SURFACE_ID))
    packets = [
        PointerDeltaPacket("raw-mouse-b", "move", 20, -10, target, 1),
        PointerDeltaPacket("raw-mouse-b", "left_down", 0, 0, target, 2),
        PointerDeltaPacket("raw-mouse-b", "move", 5, 5, target, 3),
        PointerDeltaPacket("raw-mouse-b", "left_up", 0, 0, target, 4),
    ]
    normalized_events = [event for packet in packets if (event := tracker.normalize(packet, assignments)) is not None]
    routed_events = router.route_many(normalized_events)
    surface_events = [
        surface_event
        for sequence, (packet, routed) in enumerate(zip(packets, routed_events))
        if (surface_event := surface_event_from_routed(packet, routed, sequence=sequence, surface_id=DEFAULT_GODOT_SURFACE_ID))
        is not None
    ]
    return packets, normalized_events, routed_events, surface_events


def _godot_target(surface_id: str) -> TargetIdentity:
    return TargetIdentity(
        target_id="godot-alienhand-surface",
        process_name="godot",
        window_title="Godot AlienHand Surface",
        surface_id=surface_id,
        supports_independent_pointers=True,
    )


def _godot_policy_table(surface_id: str) -> TargetPolicyTable:
    table = TargetPolicyTable()
    table.add_rule(
        TargetPolicyRule(
            rule_id="godot-alienhand-surface",
            process_name="godot",
            surface_id=surface_id,
            policy=TargetPointerPolicy("independent"),
        )
    )
    return table


def _wait_for_client(server: SurfaceEventJSONLServer) -> None:
    for _attempt in range(100):
        if server.client_count > 0:
            return
        sleep(0.01)
    raise RuntimeError("surface bridge client did not connect")


def _read_jsonl_messages(client: socket.socket, expected_count: int) -> list[JsonDict]:
    client.settimeout(2.0)
    buffer = b""
    messages: list[JsonDict] = []
    while len(messages) < expected_count:
        chunk = client.recv(4096)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line.strip():
                messages.append(json.loads(line.decode("utf-8")))
    return messages


def _default_bounds() -> ScreenBounds:
    return ScreenBounds(left=0, top=0, right=1920, bottom=1080)


def _initial_positions(assignments: DeviceAssignmentTable) -> dict[str, tuple[int, int]]:
    return {assignment.logical_device_id: (960, 540) for assignment in assignments.assignments}
