from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
import json
from pathlib import Path
from typing import Any, Iterable


JsonDict = dict[str, Any]

MODE_BLOCKED = "blocked"
MODE_INTEGRATED = "integrated"
MODE_INDEPENDENT = "independent"

ROLE_WINDOWS_POINTER = "windows_pointer"
ROLE_ALIENHAND_POINTER = "alienhand_pointer"
ROLE_OBSERVED = "observed"

CHANNEL_WINDOWS = "windows_passthrough"
CHANNEL_BLOCKED = "blocked"
CHANNEL_LEGACY = "legacy_injected"
CHANNEL_ALIENHAND = "alienhand_independent"

BUTTON_ACTIONS = {
    "left_down": ("left", True),
    "left_up": ("left", False),
    "right_down": ("right", True),
    "right_up": ("right", False),
    "middle_down": ("middle", True),
    "middle_up": ("middle", False),
}

VALID_MODES = {MODE_BLOCKED, MODE_INTEGRATED, MODE_INDEPENDENT}
VALID_DEVICE_ROLES = {ROLE_WINDOWS_POINTER, ROLE_ALIENHAND_POINTER, ROLE_OBSERVED}

RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2
RIDI_DEVICENAME = 0x20000007


@dataclass(frozen=True)
class TargetIdentity:
    target_id: str
    process_name: str
    window_title: str = ""
    surface_id: str = ""
    supports_independent_pointers: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "target_id": self.target_id,
            "process_name": self.process_name,
            "window_title": self.window_title,
            "surface_id": self.surface_id,
            "supports_independent_pointers": self.supports_independent_pointers,
        }


@dataclass(frozen=True)
class RawInputDevice:
    handle: int
    kind: str
    name: str

    def to_dict(self) -> JsonDict:
        return {"handle": self.handle, "kind": self.kind, "name": self.name}


@dataclass(frozen=True)
class MouseDeviceAssignment:
    raw_input_name: str
    logical_device_id: str
    role: str
    label: str = ""

    def __post_init__(self) -> None:
        if self.role not in VALID_DEVICE_ROLES:
            raise ValueError(f"Unsupported mouse device role: {self.role}")
        if not self.raw_input_name:
            raise ValueError("raw_input_name must be non-empty")
        if not self.logical_device_id:
            raise ValueError("logical_device_id must be non-empty")

    def to_dict(self) -> JsonDict:
        return {
            "raw_input_name": self.raw_input_name,
            "logical_device_id": self.logical_device_id,
            "role": self.role,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> MouseDeviceAssignment:
        return cls(
            raw_input_name=_string_value(data.get("raw_input_name"), "raw_input_name"),
            logical_device_id=_string_value(data.get("logical_device_id"), "logical_device_id"),
            role=_string_value(data.get("role"), "role"),
            label=_optional_string_value(data.get("label"), "label"),
        )


@dataclass
class DeviceAssignmentTable:
    assignments: list[MouseDeviceAssignment] = field(default_factory=list)

    def add_assignment(self, assignment: MouseDeviceAssignment) -> None:
        self.assignments.append(assignment)

    def resolve_raw_name(self, raw_input_name: str) -> MouseDeviceAssignment | None:
        for assignment in self.assignments:
            if assignment.raw_input_name == raw_input_name:
                return assignment
        return None

    def captured_device_ids(self) -> set[str]:
        return {
            assignment.logical_device_id
            for assignment in self.assignments
            if assignment.role == ROLE_ALIENHAND_POINTER
        }

    def to_dict(self) -> JsonDict:
        return {"assignments": [assignment.to_dict() for assignment in self.assignments]}

    @classmethod
    def from_dict(cls, data: JsonDict) -> DeviceAssignmentTable:
        assignments = data.get("assignments", [])
        if not isinstance(assignments, list):
            raise ValueError("assignments must be a list")
        table = cls()
        for assignment_data in assignments:
            if not isinstance(assignment_data, dict):
                raise ValueError("each assignment must be an object")
            table.add_assignment(MouseDeviceAssignment.from_dict(assignment_data))
        return table


@dataclass(frozen=True)
class CaptureBackendStatus:
    backend_name: str
    available: bool
    can_block_input: bool
    active_device_ids: tuple[str, ...] = ()
    reason: str = ""
    simulation_only: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "backend_name": self.backend_name,
            "available": self.available,
            "can_block_input": self.can_block_input,
            "active_device_ids": list(self.active_device_ids),
            "reason": self.reason,
            "simulation_only": self.simulation_only,
        }


class UnavailableCaptureBackend:
    backend_name = "unavailable"

    def status(self, assignments: DeviceAssignmentTable) -> CaptureBackendStatus:
        return CaptureBackendStatus(
            backend_name=self.backend_name,
            available=False,
            can_block_input=False,
            active_device_ids=tuple(sorted(assignments.captured_device_ids())),
            reason="No driver, filter, or interception backend is configured.",
        )

    def activate(self, assignments: DeviceAssignmentTable) -> CaptureBackendStatus:
        status = self.status(assignments)
        raise RuntimeError(status.reason)


class SimulatedCaptureBackend:
    backend_name = "simulated"

    def __init__(self) -> None:
        self.active_device_ids: set[str] = set()

    def status(self, assignments: DeviceAssignmentTable) -> CaptureBackendStatus:
        captured = assignments.captured_device_ids()
        return CaptureBackendStatus(
            backend_name=self.backend_name,
            available=True,
            can_block_input=True,
            active_device_ids=tuple(sorted(self.active_device_ids or captured)),
            reason="Simulation backend only; no real hardware input is blocked.",
            simulation_only=True,
        )

    def activate(self, assignments: DeviceAssignmentTable) -> CaptureBackendStatus:
        self.active_device_ids = assignments.captured_device_ids()
        return self.status(assignments)


@dataclass(frozen=True)
class TargetPointerPolicy:
    mode: str
    legacy_fallback: str = MODE_BLOCKED

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"Unsupported target pointer mode: {self.mode}")
        if self.legacy_fallback not in {MODE_BLOCKED, MODE_INTEGRATED}:
            raise ValueError("legacy_fallback must be blocked or integrated")

    def to_dict(self) -> JsonDict:
        return {"mode": self.mode, "legacy_fallback": self.legacy_fallback}


@dataclass(frozen=True)
class TargetPolicyRule:
    rule_id: str
    policy: TargetPointerPolicy
    target_id: str = ""
    process_name: str = ""
    window_title_contains: str = ""
    surface_id: str = ""

    def matches(self, target: TargetIdentity) -> bool:
        if self.target_id and target.target_id != self.target_id:
            return False
        if self.process_name and target.process_name.lower() != self.process_name.lower():
            return False
        if self.window_title_contains and self.window_title_contains.lower() not in target.window_title.lower():
            return False
        if self.surface_id and target.surface_id != self.surface_id:
            return False
        return any((self.target_id, self.process_name, self.window_title_contains, self.surface_id))

    def specificity(self) -> tuple[int, int]:
        score = 0
        score += 8 if self.target_id else 0
        score += 4 if self.surface_id else 0
        score += 2 if self.process_name else 0
        score += 1 if self.window_title_contains else 0
        return score, len(self.rule_id)

    def to_dict(self) -> JsonDict:
        return {
            "rule_id": self.rule_id,
            "policy": self.policy.to_dict(),
            "target_id": self.target_id,
            "process_name": self.process_name,
            "window_title_contains": self.window_title_contains,
            "surface_id": self.surface_id,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> TargetPolicyRule:
        policy_data = _object_value(data.get("policy"), "policy")
        return cls(
            rule_id=_string_value(data.get("rule_id"), "rule_id"),
            policy=TargetPointerPolicy(
                mode=_string_value(policy_data.get("mode"), "policy.mode"),
                legacy_fallback=_string_value(
                    policy_data.get("legacy_fallback", MODE_BLOCKED),
                    "policy.legacy_fallback",
                ),
            ),
            target_id=_optional_string_value(data.get("target_id"), "target_id"),
            process_name=_optional_string_value(data.get("process_name"), "process_name"),
            window_title_contains=_optional_string_value(data.get("window_title_contains"), "window_title_contains"),
            surface_id=_optional_string_value(data.get("surface_id"), "surface_id"),
        )


@dataclass
class TargetPolicyTable:
    default_captured_policy: TargetPointerPolicy = field(default_factory=lambda: TargetPointerPolicy(MODE_BLOCKED))
    policies: dict[str, TargetPointerPolicy] = field(default_factory=dict)
    rules: list[TargetPolicyRule] = field(default_factory=list)

    def set_policy(self, target_id: str, policy: TargetPointerPolicy) -> None:
        self.policies[target_id] = policy

    def add_rule(self, rule: TargetPolicyRule) -> None:
        self.rules.append(rule)

    def resolve(self, target: TargetIdentity) -> TargetPointerPolicy:
        if target.target_id in self.policies:
            return self.policies[target.target_id]
        matched_rules = [rule for rule in self.rules if rule.matches(target)]
        if matched_rules:
            return max(matched_rules, key=lambda rule: rule.specificity()).policy
        return self.default_captured_policy

    def to_dict(self) -> JsonDict:
        return {
            "default_captured_policy": self.default_captured_policy.to_dict(),
            "policies": {target_id: policy.to_dict() for target_id, policy in sorted(self.policies.items())},
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> TargetPolicyTable:
        default_policy_data = _object_value(data.get("default_captured_policy", {}), "default_captured_policy")
        table = cls(
            default_captured_policy=TargetPointerPolicy(
                mode=_string_value(default_policy_data.get("mode", MODE_BLOCKED), "default_captured_policy.mode"),
                legacy_fallback=_string_value(
                    default_policy_data.get("legacy_fallback", MODE_BLOCKED),
                    "default_captured_policy.legacy_fallback",
                ),
            )
        )
        policies = _object_value(data.get("policies", {}), "policies")
        for target_id, policy_data in policies.items():
            if not isinstance(policy_data, dict):
                raise ValueError(f"policies.{target_id} must be an object")
            table.set_policy(
                target_id,
                TargetPointerPolicy(
                    mode=_string_value(policy_data.get("mode"), f"policies.{target_id}.mode"),
                    legacy_fallback=_string_value(
                        policy_data.get("legacy_fallback", MODE_BLOCKED),
                        f"policies.{target_id}.legacy_fallback",
                    ),
                ),
            )
        rules = data.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("rules must be a list")
        for rule_data in rules:
            if not isinstance(rule_data, dict):
                raise ValueError("each rule must be an object")
            table.add_rule(TargetPolicyRule.from_dict(rule_data))
        return table


@dataclass(frozen=True)
class PointerInputEvent:
    device_id: str
    action: str
    x: int
    y: int
    target: TargetIdentity
    timestamp_ms: int
    wheel_delta: int = 0

    def to_dict(self) -> JsonDict:
        return {
            "device_id": self.device_id,
            "action": self.action,
            "x": self.x,
            "y": self.y,
            "target": self.target.to_dict(),
            "timestamp_ms": self.timestamp_ms,
            "wheel_delta": self.wheel_delta,
        }


@dataclass(frozen=True)
class ScreenBounds:
    left: int
    top: int
    right: int
    bottom: int

    def clamp(self, x: int, y: int) -> tuple[int, int]:
        return min(max(x, self.left), self.right), min(max(y, self.top), self.bottom)

    def to_dict(self) -> JsonDict:
        return {"left": self.left, "top": self.top, "right": self.right, "bottom": self.bottom}


@dataclass(frozen=True)
class PointerDeltaPacket:
    raw_input_name: str
    action: str
    dx: int
    dy: int
    target: TargetIdentity
    timestamp_ms: int
    wheel_delta: int = 0

    def to_dict(self) -> JsonDict:
        return {
            "raw_input_name": self.raw_input_name,
            "action": self.action,
            "dx": self.dx,
            "dy": self.dy,
            "target": self.target.to_dict(),
            "timestamp_ms": self.timestamp_ms,
            "wheel_delta": self.wheel_delta,
        }


@dataclass
class PointerState:
    pointer_id: str
    x: int = 0
    y: int = 0
    pressed_buttons: set[str] = field(default_factory=set)

    def apply(self, event: PointerInputEvent) -> None:
        self.x = event.x
        self.y = event.y
        button_action = BUTTON_ACTIONS.get(event.action)
        if button_action is None:
            return
        button, is_down = button_action
        if is_down:
            self.pressed_buttons.add(button)
        else:
            self.pressed_buttons.discard(button)

    def to_dict(self) -> JsonDict:
        return {
            "pointer_id": self.pointer_id,
            "x": self.x,
            "y": self.y,
            "pressed_buttons": sorted(self.pressed_buttons),
        }


class VirtualPointerTracker:
    def __init__(self, bounds: ScreenBounds, initial_positions: dict[str, tuple[int, int]] | None = None) -> None:
        self.bounds = bounds
        self.states: dict[str, PointerState] = {}
        for device_id, (x, y) in (initial_positions or {}).items():
            clamped_x, clamped_y = self.bounds.clamp(x, y)
            self.states[device_id] = PointerState(device_id, clamped_x, clamped_y)

    def normalize(self, packet: PointerDeltaPacket, assignments: DeviceAssignmentTable) -> PointerInputEvent | None:
        assignment = assignments.resolve_raw_name(packet.raw_input_name)
        if assignment is None:
            return None
        state = self.states.setdefault(assignment.logical_device_id, PointerState(assignment.logical_device_id))
        x, y = self.bounds.clamp(state.x + packet.dx, state.y + packet.dy)
        event = PointerInputEvent(
            device_id=assignment.logical_device_id,
            action=packet.action,
            x=x,
            y=y,
            target=packet.target,
            timestamp_ms=packet.timestamp_ms,
            wheel_delta=packet.wheel_delta,
        )
        state.apply(event)
        return event

    def state_snapshot(self) -> JsonDict:
        return {
            "bounds": self.bounds.to_dict(),
            "states": {device_id: state.to_dict() for device_id, state in sorted(self.states.items())},
        }


@dataclass(frozen=True)
class RoutedPointerEvent:
    channel: str
    source_device_id: str
    pointer_id: str
    target_id: str
    configured_mode: str
    effective_mode: str
    action: str
    x: int
    y: int
    timestamp_ms: int
    delivered: bool
    details: JsonDict

    def to_dict(self) -> JsonDict:
        return {
            "channel": self.channel,
            "source_device_id": self.source_device_id,
            "pointer_id": self.pointer_id,
            "target_id": self.target_id,
            "configured_mode": self.configured_mode,
            "effective_mode": self.effective_mode,
            "action": self.action,
            "x": self.x,
            "y": self.y,
            "timestamp_ms": self.timestamp_ms,
            "delivered": self.delivered,
            "details": self.details,
        }


@dataclass(frozen=True)
class LegacyInjectionAction:
    action: str
    x: int
    y: int
    target_id: str
    source_device_id: str
    pointer_id: str
    timestamp_ms: int
    button: str = ""
    wheel_delta: int = 0

    def to_dict(self) -> JsonDict:
        return {
            "action": self.action,
            "x": self.x,
            "y": self.y,
            "target_id": self.target_id,
            "source_device_id": self.source_device_id,
            "pointer_id": self.pointer_id,
            "timestamp_ms": self.timestamp_ms,
            "button": self.button,
            "wheel_delta": self.wheel_delta,
        }


class VirtualPointerRouter:
    """Hardware-free router for proving AlienHand pointer policy semantics."""

    def __init__(self, captured_device_ids: Iterable[str], policy_table: TargetPolicyTable | None = None) -> None:
        self.captured_device_ids = set(captured_device_ids)
        self.policy_table = policy_table or TargetPolicyTable()
        self.windows_pointer = PointerState("windows")
        self.virtual_pointers = {
            device_id: PointerState(f"alienhand:{device_id}") for device_id in sorted(self.captured_device_ids)
        }
        self.active_legacy_device_id: str | None = None

    def route(self, event: PointerInputEvent) -> RoutedPointerEvent:
        if event.device_id not in self.captured_device_ids:
            self.windows_pointer.apply(event)
            return RoutedPointerEvent(
                channel=CHANNEL_WINDOWS,
                source_device_id=event.device_id,
                pointer_id=self.windows_pointer.pointer_id,
                target_id=event.target.target_id,
                configured_mode=MODE_INTEGRATED,
                effective_mode=MODE_INTEGRATED,
                action=event.action,
                x=event.x,
                y=event.y,
                timestamp_ms=event.timestamp_ms,
                delivered=True,
                details={"source": "windows_normal_pointer"},
            )

        pointer = self._virtual_pointer_for(event.device_id)
        pointer.apply(event)
        policy = self.policy_table.resolve(event.target)
        effective_mode, fallback_reason = effective_target_mode(policy, event.target)
        if effective_mode == MODE_BLOCKED:
            return self._blocked_event(event, pointer, policy.mode, effective_mode, fallback_reason)
        if effective_mode == MODE_INDEPENDENT:
            return self._independent_event(event, pointer, policy.mode, effective_mode)
        return self._legacy_event(event, pointer, policy.mode, effective_mode, fallback_reason)

    def route_many(self, events: Iterable[PointerInputEvent]) -> list[RoutedPointerEvent]:
        return [self.route(event) for event in events]

    def state_snapshot(self) -> JsonDict:
        return {
            "windows_pointer": self.windows_pointer.to_dict(),
            "virtual_pointers": {
                device_id: pointer.to_dict() for device_id, pointer in sorted(self.virtual_pointers.items())
            },
            "active_legacy_device_id": self.active_legacy_device_id,
        }

    def _virtual_pointer_for(self, device_id: str) -> PointerState:
        if device_id not in self.virtual_pointers:
            self.virtual_pointers[device_id] = PointerState(f"alienhand:{device_id}")
        return self.virtual_pointers[device_id]

    def _blocked_event(
        self,
        event: PointerInputEvent,
        pointer: PointerState,
        configured_mode: str,
        effective_mode: str,
        fallback_reason: str | None,
    ) -> RoutedPointerEvent:
        details: JsonDict = {
            "source": "captured_mouse",
            "virtual_state": pointer.to_dict(),
        }
        if fallback_reason is not None:
            details["fallback_reason"] = fallback_reason
        return RoutedPointerEvent(
            channel=CHANNEL_BLOCKED,
            source_device_id=event.device_id,
            pointer_id=pointer.pointer_id,
            target_id=event.target.target_id,
            configured_mode=configured_mode,
            effective_mode=effective_mode,
            action=event.action,
            x=event.x,
            y=event.y,
            timestamp_ms=event.timestamp_ms,
            delivered=False,
            details=details,
        )

    def _independent_event(
        self,
        event: PointerInputEvent,
        pointer: PointerState,
        configured_mode: str,
        effective_mode: str,
    ) -> RoutedPointerEvent:
        return RoutedPointerEvent(
            channel=CHANNEL_ALIENHAND,
            source_device_id=event.device_id,
            pointer_id=pointer.pointer_id,
            target_id=event.target.target_id,
            configured_mode=configured_mode,
            effective_mode=effective_mode,
            action=event.action,
            x=event.x,
            y=event.y,
            timestamp_ms=event.timestamp_ms,
            delivered=True,
            details={
                "source": "alienhand_pointer_event",
                "virtual_state": pointer.to_dict(),
            },
        )

    def _legacy_event(
        self,
        event: PointerInputEvent,
        pointer: PointerState,
        configured_mode: str,
        effective_mode: str,
        fallback_reason: str | None,
    ) -> RoutedPointerEvent:
        self._update_legacy_gesture_owner(event)
        details: JsonDict = {
            "source": "anonymous_legacy_mouse_stream",
            "virtual_state": pointer.to_dict(),
            "cursor_policy": "move_restore_or_overlay_mask",
            "active_legacy_device_id": self.active_legacy_device_id,
        }
        if event.action in {"wheel", "horizontal_wheel"}:
            details["wheel_delta"] = event.wheel_delta
        if fallback_reason is not None:
            details["fallback_reason"] = fallback_reason
        return RoutedPointerEvent(
            channel=CHANNEL_LEGACY,
            source_device_id=event.device_id,
            pointer_id=pointer.pointer_id,
            target_id=event.target.target_id,
            configured_mode=configured_mode,
            effective_mode=effective_mode,
            action=event.action,
            x=event.x,
            y=event.y,
            timestamp_ms=event.timestamp_ms,
            delivered=True,
            details=details,
        )

    def _update_legacy_gesture_owner(self, event: PointerInputEvent) -> None:
        button_action = BUTTON_ACTIONS.get(event.action)
        if button_action is None:
            return
        _button, is_down = button_action
        if is_down:
            self.active_legacy_device_id = event.device_id
            return
        pointer = self._virtual_pointer_for(event.device_id)
        if not pointer.pressed_buttons and self.active_legacy_device_id == event.device_id:
            self.active_legacy_device_id = None


def effective_target_mode(policy: TargetPointerPolicy, target: TargetIdentity) -> tuple[str, str | None]:
    if policy.mode == MODE_INDEPENDENT and not target.supports_independent_pointers:
        return policy.legacy_fallback, "target_not_independent_aware"
    return policy.mode, None


def summarize_routed_events(events: Iterable[RoutedPointerEvent]) -> JsonDict:
    counts = {
        CHANNEL_WINDOWS: 0,
        CHANNEL_BLOCKED: 0,
        CHANNEL_LEGACY: 0,
        CHANNEL_ALIENHAND: 0,
    }
    delivered = 0
    for event in events:
        counts[event.channel] = counts.get(event.channel, 0) + 1
        if event.delivered:
            delivered += 1
    return {
        "channels": counts,
        "delivered_events": delivered,
    }


class RecordingLegacyInjectionBackend:
    def __init__(self) -> None:
        self.actions: list[LegacyInjectionAction] = []

    def inject(self, event: RoutedPointerEvent) -> list[LegacyInjectionAction]:
        actions = legacy_injection_actions(event)
        self.actions.extend(actions)
        return actions

    def inject_many(self, events: Iterable[RoutedPointerEvent]) -> list[LegacyInjectionAction]:
        injected: list[LegacyInjectionAction] = []
        for event in events:
            injected.extend(self.inject(event))
        return injected

    def to_dict(self) -> JsonDict:
        return {"actions": [action.to_dict() for action in self.actions]}


def legacy_injection_actions(event: RoutedPointerEvent) -> list[LegacyInjectionAction]:
    if event.channel != CHANNEL_LEGACY:
        return []

    move = _legacy_action(event, "move")
    if event.action == "move":
        return [move]
    if event.action in {"left_down", "right_down", "middle_down"}:
        return [move, _legacy_action(event, "button_down", button=event.action.removesuffix("_down"))]
    if event.action in {"left_up", "right_up", "middle_up"}:
        return [move, _legacy_action(event, "button_up", button=event.action.removesuffix("_up"))]
    if event.action in {"wheel", "horizontal_wheel"}:
        wheel_delta = int(event.details.get("wheel_delta", 0))
        return [move, _legacy_action(event, event.action, wheel_delta=wheel_delta)]
    return [move, _legacy_action(event, event.action)]


def enumerate_windows_raw_input_devices() -> list[RawInputDevice]:
    if not hasattr(ctypes, "windll"):
        return []

    from ctypes import wintypes

    class RAWINPUTDEVICELIST(ctypes.Structure):
        _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.GetRawInputDeviceList.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICELIST),
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputDeviceList.restype = wintypes.UINT
    user32.GetRawInputDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT)]
    user32.GetRawInputDeviceInfoW.restype = wintypes.UINT

    count = wintypes.UINT(0)
    result = user32.GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if result == ctypes.c_uint(-1).value:
        raise ctypes.WinError()
    if count.value == 0:
        return []

    raw_devices = (RAWINPUTDEVICELIST * count.value)()
    result = user32.GetRawInputDeviceList(raw_devices, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if result == ctypes.c_uint(-1).value:
        raise ctypes.WinError()

    devices: list[RawInputDevice] = []
    for raw_device in raw_devices[: result]:
        devices.append(
            RawInputDevice(
                handle=int(raw_device.hDevice or 0),
                kind=_raw_input_kind(int(raw_device.dwType)),
                name=_raw_input_device_name(user32, raw_device.hDevice),
            )
        )
    return devices


def run_multi_mouse_device_scan(root: str | Path) -> JsonDict:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    devices = enumerate_windows_raw_input_devices()
    mice = [device for device in devices if device.kind == "mouse"]
    result = {
        "root": str(root_path.resolve()),
        "devices": [device.to_dict() for device in devices],
        "mouse_devices": [device.to_dict() for device in mice],
        "mouse_device_count": len(mice),
        "multiple_mice_visible": len(mice) >= 2,
        "capture_backend_present": False,
        "scan_note": "Raw Input can identify physical mouse devices, but this scan does not block or capture input.",
    }
    output = root_path / "multi-mouse-device-scan.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["output"] = str(output.resolve())
    return result


def suggest_mouse_device_assignments(devices: Iterable[RawInputDevice]) -> DeviceAssignmentTable:
    mouse_devices = [device for device in devices if device.kind == "mouse"]
    table = DeviceAssignmentTable()
    for index, device in enumerate(mouse_devices):
        if index == 0:
            table.add_assignment(
                MouseDeviceAssignment(
                    raw_input_name=device.name,
                    logical_device_id="mouse-a",
                    role=ROLE_WINDOWS_POINTER,
                    label="Windows pointer candidate",
                )
            )
        elif index == 1:
            table.add_assignment(
                MouseDeviceAssignment(
                    raw_input_name=device.name,
                    logical_device_id="mouse-b",
                    role=ROLE_ALIENHAND_POINTER,
                    label="AlienHand pointer candidate",
                )
            )
        else:
            table.add_assignment(
                MouseDeviceAssignment(
                    raw_input_name=device.name,
                    logical_device_id=f"mouse-extra-{index + 1}",
                    role=ROLE_OBSERVED,
                    label="Observed extra mouse",
                )
            )
    return table


def run_multi_mouse_assignment_suggestion(root: str | Path) -> JsonDict:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    devices = enumerate_windows_raw_input_devices()
    table = suggest_mouse_device_assignments(devices)
    result = {
        "root": str(root_path.resolve()),
        "mouse_device_count": len([device for device in devices if device.kind == "mouse"]),
        "assignment_table": table.to_dict(),
        "captured_device_ids": sorted(table.captured_device_ids()),
        "activation_state": "suggested_only",
        "assignment_note": "Review assignments before enabling any future capture backend.",
    }
    output = root_path / "multi-mouse-assignment-suggestion.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["output"] = str(output.resolve())
    return result


def run_multi_mouse_capture_preflight(root: str | Path) -> JsonDict:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    devices = enumerate_windows_raw_input_devices()
    assignments = suggest_mouse_device_assignments(devices)
    backend = UnavailableCaptureBackend()
    status = backend.status(assignments)
    mouse_device_count = len([device for device in devices if device.kind == "mouse"])
    result = {
        "root": str(root_path.resolve()),
        "mouse_device_count": mouse_device_count,
        "multiple_mice_visible": mouse_device_count >= 2,
        "assignment_table": assignments.to_dict(),
        "capture_backend": status.to_dict(),
        "can_activate_capture": status.available and status.can_block_input and bool(status.active_device_ids),
        "preflight_note": "This preflight intentionally refuses real capture until a backend is installed and verified.",
    }
    output = root_path / "multi-mouse-capture-preflight.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["output"] = str(output.resolve())
    return result


def read_policy_table(path: str | Path) -> TargetPolicyTable:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("policy table document must be an object")
    return TargetPolicyTable.from_dict(data)


def write_policy_table(path: str | Path, policy_table: TargetPolicyTable) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(policy_table.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return output


def read_device_assignment_table(path: str | Path) -> DeviceAssignmentTable:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("device assignment document must be an object")
    return DeviceAssignmentTable.from_dict(data)


def write_device_assignment_table(path: str | Path, table: DeviceAssignmentTable) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(table.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return output


def run_multi_mouse_virtualization_proof(root: str | Path) -> JsonDict:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)

    legacy_target = TargetIdentity(
        target_id="legacy-editor",
        process_name="notepad.exe",
        window_title="Legacy editor",
        supports_independent_pointers=False,
    )
    alienhand_target = TargetIdentity(
        target_id="alienhand-workbench",
        process_name="AlienHand.exe",
        window_title="AlienHand Workbench",
        surface_id="chat",
        supports_independent_pointers=True,
    )
    blocked_target = TargetIdentity(
        target_id="secure-login",
        process_name="credential-ui.exe",
        window_title="Secure login",
        supports_independent_pointers=False,
    )
    fallback_target = TargetIdentity(
        target_id="legacy-independent-request",
        process_name="paint.exe",
        window_title="Legacy fallback",
        supports_independent_pointers=False,
    )

    policy_table = TargetPolicyTable()
    policy_table.set_policy(legacy_target.target_id, TargetPointerPolicy(MODE_INTEGRATED))
    policy_table.set_policy(alienhand_target.target_id, TargetPointerPolicy(MODE_INDEPENDENT))
    policy_table.set_policy(blocked_target.target_id, TargetPointerPolicy(MODE_BLOCKED))
    policy_table.set_policy(fallback_target.target_id, TargetPointerPolicy(MODE_INDEPENDENT, MODE_INTEGRATED))

    router = VirtualPointerRouter(captured_device_ids={"mouse-b"}, policy_table=policy_table)
    inputs = [
        PointerInputEvent("mouse-a", "move", 100, 120, legacy_target, 1),
        PointerInputEvent("mouse-a", "left_down", 100, 120, legacy_target, 2),
        PointerInputEvent("mouse-a", "left_up", 100, 120, legacy_target, 3),
        PointerInputEvent("mouse-b", "move", 20, 25, blocked_target, 4),
        PointerInputEvent("mouse-b", "move", 500, 320, alienhand_target, 5),
        PointerInputEvent("mouse-b", "left_down", 500, 320, alienhand_target, 6),
        PointerInputEvent("mouse-b", "left_up", 500, 320, alienhand_target, 7),
        PointerInputEvent("mouse-b", "move", 640, 410, legacy_target, 8),
        PointerInputEvent("mouse-b", "left_down", 640, 410, legacy_target, 9),
        PointerInputEvent("mouse-b", "left_up", 640, 410, legacy_target, 10),
        PointerInputEvent("mouse-b", "move", 700, 420, fallback_target, 11),
    ]
    routed = router.route_many(inputs)
    injection_backend = RecordingLegacyInjectionBackend()
    legacy_actions = injection_backend.inject_many(routed)
    summary = summarize_routed_events(routed)
    state = router.state_snapshot()
    events_by_channel = summary["channels"]
    routing_modes_ok = (
        events_by_channel[CHANNEL_WINDOWS] == 3
        and events_by_channel[CHANNEL_BLOCKED] == 1
        and events_by_channel[CHANNEL_ALIENHAND] == 3
        and events_by_channel[CHANNEL_LEGACY] == 4
        and state["windows_pointer"]["x"] == 100
        and state["windows_pointer"]["y"] == 120
        and state["virtual_pointers"]["mouse-b"]["x"] == 700
        and state["virtual_pointers"]["mouse-b"]["y"] == 420
        and len(legacy_actions) == 6
        and any(event.details.get("fallback_reason") == "target_not_independent_aware" for event in routed)
    )
    result = {
        "root": str(root_path.resolve()),
        "hardware_free": True,
        "captured_device_ids": sorted(router.captured_device_ids),
        "policy_table": policy_table.to_dict(),
        "summary": summary,
        "state": state,
        "inputs": [event.to_dict() for event in inputs],
        "routed": [event.to_dict() for event in routed],
        "legacy_injection_actions": [action.to_dict() for action in legacy_actions],
        "routing_modes_ok": routing_modes_ok,
    }
    output = root_path / "multi-mouse-virtualization-proof.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["output"] = str(output.resolve())
    return result


def run_multi_mouse_pipeline_proof(root: str | Path) -> JsonDict:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    legacy_target = TargetIdentity(
        target_id="legacy-editor",
        process_name="notepad.exe",
        window_title="Legacy editor",
        supports_independent_pointers=False,
    )
    alienhand_target = TargetIdentity(
        target_id="alienhand-workbench",
        process_name="AlienHand.exe",
        window_title="AlienHand Workbench",
        surface_id="chat",
        supports_independent_pointers=True,
    )
    assignments = DeviceAssignmentTable(
        [
            MouseDeviceAssignment("raw-mouse-a", "mouse-a", ROLE_WINDOWS_POINTER),
            MouseDeviceAssignment("raw-mouse-b", "mouse-b", ROLE_ALIENHAND_POINTER),
        ]
    )
    policies = TargetPolicyTable()
    policies.add_rule(
        TargetPolicyRule(
            rule_id="legacy-editors",
            process_name="notepad.exe",
            policy=TargetPointerPolicy(MODE_INTEGRATED),
        )
    )
    policies.add_rule(
        TargetPolicyRule(
            rule_id="alienhand-chat",
            process_name="AlienHand.exe",
            surface_id="chat",
            policy=TargetPointerPolicy(MODE_INDEPENDENT),
        )
    )
    tracker = VirtualPointerTracker(
        ScreenBounds(left=0, top=0, right=800, bottom=600),
        initial_positions={"mouse-a": (100, 100), "mouse-b": (400, 300)},
    )
    router = VirtualPointerRouter(captured_device_ids=assignments.captured_device_ids(), policy_table=policies)
    packets = [
        PointerDeltaPacket("raw-mouse-a", "move", 10, 0, legacy_target, 1),
        PointerDeltaPacket("raw-mouse-b", "move", 50, 10, alienhand_target, 2),
        PointerDeltaPacket("raw-mouse-b", "left_down", 0, 0, alienhand_target, 3),
        PointerDeltaPacket("raw-mouse-b", "left_up", 0, 0, alienhand_target, 4),
        PointerDeltaPacket("raw-mouse-b", "move", 100, 50, legacy_target, 5),
        PointerDeltaPacket("raw-mouse-b", "left_down", 0, 0, legacy_target, 6),
        PointerDeltaPacket("raw-mouse-b", "left_up", 0, 0, legacy_target, 7),
    ]
    normalized_events = [event for packet in packets if (event := tracker.normalize(packet, assignments)) is not None]
    routed_events = router.route_many(normalized_events)
    legacy_backend = RecordingLegacyInjectionBackend()
    legacy_actions = legacy_backend.inject_many(routed_events)
    summary = summarize_routed_events(routed_events)
    pipeline_ok = (
        len(normalized_events) == len(packets)
        and summary["channels"][CHANNEL_WINDOWS] == 1
        and summary["channels"][CHANNEL_ALIENHAND] == 3
        and summary["channels"][CHANNEL_LEGACY] == 3
        and len(legacy_actions) == 5
        and tracker.state_snapshot()["states"]["mouse-b"]["x"] == 550
        and tracker.state_snapshot()["states"]["mouse-b"]["y"] == 360
    )
    result = {
        "root": str(root_path.resolve()),
        "hardware_free": True,
        "assignments": assignments.to_dict(),
        "policies": policies.to_dict(),
        "tracker_state": tracker.state_snapshot(),
        "normalized_events": [event.to_dict() for event in normalized_events],
        "routed_events": [event.to_dict() for event in routed_events],
        "legacy_injection_actions": [action.to_dict() for action in legacy_actions],
        "summary": summary,
        "pipeline_ok": pipeline_ok,
    }
    output = root_path / "multi-mouse-pipeline-proof.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["output"] = str(output.resolve())
    return result


def _raw_input_kind(kind: int) -> str:
    if kind == RIM_TYPEMOUSE:
        return "mouse"
    if kind == RIM_TYPEKEYBOARD:
        return "keyboard"
    if kind == RIM_TYPEHID:
        return "hid"
    return f"unknown:{kind}"


def _raw_input_device_name(user32: Any, handle: int) -> str:
    from ctypes import wintypes

    size = wintypes.UINT(0)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(size))
    if size.value == 0:
        return ""
    buffer = ctypes.create_unicode_buffer(size.value + 1)
    result = user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buffer, ctypes.byref(size))
    if result == ctypes.c_uint(-1).value:
        raise ctypes.WinError()
    return buffer.value


def _legacy_action(
    event: RoutedPointerEvent,
    action: str,
    *,
    button: str = "",
    wheel_delta: int = 0,
) -> LegacyInjectionAction:
    return LegacyInjectionAction(
        action=action,
        x=event.x,
        y=event.y,
        target_id=event.target_id,
        source_device_id=event.source_device_id,
        pointer_id=event.pointer_id,
        timestamp_ms=event.timestamp_ms,
        button=button,
        wheel_delta=wheel_delta,
    )


def _object_value(value: Any, name: str) -> JsonDict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _string_value(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string_value(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value
