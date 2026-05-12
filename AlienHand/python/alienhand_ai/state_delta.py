from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class StateDelta:
    added: JsonDict
    changed: JsonDict
    removed: JsonDict

    def to_dict(self) -> JsonDict:
        return {
            "added": self.added,
            "changed": self.changed,
            "removed": self.removed,
            "summary": summarize_delta(self),
        }


def build_state_snapshot(
    state: JsonDict | None = None,
    observation: JsonDict | None = None,
    variables: JsonDict | None = None,
    resources: JsonDict | None = None,
    decision: JsonDict | None = None,
    action: JsonDict | None = None,
    debug: JsonDict | None = None,
) -> JsonDict:
    return {
        "state": state or {},
        "observation": observation or {},
        "variables": variables or {},
        "resources": resources or {},
        "decision": decision,
        "action": action,
        "debug": debug or {},
    }


def diff_state(previous: JsonDict | None, current: JsonDict) -> StateDelta:
    if previous is None:
        return StateDelta(added=current, changed={}, removed={})
    added: JsonDict = {}
    changed: JsonDict = {}
    removed: JsonDict = {}
    _diff_mapping(previous, current, added, changed, removed)
    return StateDelta(added=added, changed=changed, removed=removed)


def summarize_delta(delta: StateDelta | JsonDict) -> JsonDict:
    payload = {"added": delta.added, "changed": delta.changed, "removed": delta.removed} if isinstance(delta, StateDelta) else delta
    return {
        "added": _leaf_count(payload.get("added", {})),
        "changed": _leaf_count(payload.get("changed", {})),
        "removed": _leaf_count(payload.get("removed", {})),
    }


def _diff_mapping(previous: JsonDict, current: JsonDict, added: JsonDict, changed: JsonDict, removed: JsonDict) -> None:
    previous_keys = set(previous)
    current_keys = set(current)
    for key in sorted(current_keys - previous_keys):
        added[key] = current[key]
    for key in sorted(previous_keys - current_keys):
        removed[key] = previous[key]
    for key in sorted(previous_keys & current_keys):
        previous_value = previous[key]
        current_value = current[key]
        if previous_value == current_value:
            continue
        if isinstance(previous_value, dict) and isinstance(current_value, dict):
            child_added: JsonDict = {}
            child_changed: JsonDict = {}
            child_removed: JsonDict = {}
            _diff_mapping(previous_value, current_value, child_added, child_changed, child_removed)
            if child_added:
                added[key] = child_added
            if child_changed:
                changed[key] = child_changed
            if child_removed:
                removed[key] = child_removed
        else:
            changed[key] = {"from": previous_value, "to": current_value}


def _leaf_count(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, dict):
        return 1
    if set(value) == {"from", "to"}:
        return 1
    return sum(_leaf_count(child) for child in value.values())
