from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html import escape
import json
from pathlib import Path
import shutil
from time import time
from typing import Any

from .state_delta import build_state_snapshot, diff_state


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class DecisionStep:
    label: str
    inputs: JsonDict = field(default_factory=dict)
    output: JsonDict = field(default_factory=dict)
    confidence: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class DecisionTrace:
    policy: str
    steps: tuple[DecisionStep, ...] = ()
    selected_action: JsonDict | None = None
    rejected_actions: tuple[JsonDict, ...] = ()


@dataclass(frozen=True)
class TelemetryFrame:
    index: int
    timestamp: float
    image: str | None
    state: JsonDict
    delta: JsonDict
    observation: JsonDict
    variables: JsonDict
    resources: JsonDict
    decision: JsonDict | None
    action: JsonDict | None
    debug: JsonDict


class TelemetryRecorder:
    def __init__(self, root: str | Path, run_name: str | None = "auto", metadata: JsonDict | None = None) -> None:
        self.root = Path(root)
        self.run_name = resolve_run_name(self.root, run_name)
        self.run_dir = self.root / self.run_name
        self.frames_dir = self.run_dir / "frames"
        self.events_path = self.run_dir / "events.jsonl"
        self.manifest_path = self.run_dir / "manifest.json"
        self.frame_index = 0
        self.metadata = metadata or {}
        self._last_state_snapshot: JsonDict | None = None
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest()

    def log_event(self, kind: str, payload: JsonDict | None = None, frame_index: int | None = None) -> None:
        self._append_event(
            {
                "type": "event",
                "kind": kind,
                "timestamp": time(),
                "frame_index": frame_index,
                "payload": payload or {},
            }
        )

    def record_frame(
        self,
        image_path: str | Path | None = None,
        state: JsonDict | None = None,
        observation: JsonDict | None = None,
        variables: JsonDict | None = None,
        resources: JsonDict | None = None,
        decision: DecisionTrace | JsonDict | None = None,
        action: JsonDict | None = None,
        debug: JsonDict | None = None,
    ) -> TelemetryFrame:
        self.frame_index += 1
        image = self._copy_frame_image(image_path, self.frame_index) if image_path else None
        decision_payload = _decision_to_dict(decision) if decision is not None else None
        state_snapshot = build_state_snapshot(
            state=state,
            observation=observation,
            variables=variables,
            resources=resources,
            decision=decision_payload,
            action=action,
            debug=debug,
        )
        delta_payload = diff_state(self._last_state_snapshot, state_snapshot).to_dict()
        self._last_state_snapshot = state_snapshot
        frame = TelemetryFrame(
            index=self.frame_index,
            timestamp=time(),
            image=image,
            state=state_snapshot["state"],
            delta=delta_payload,
            observation=observation or {},
            variables=variables or {},
            resources=resources or {},
            decision=decision_payload,
            action=action,
            debug=debug or {},
        )
        self._append_event({"type": "frame", **asdict(frame)})
        return frame

    def write_report(self, output: str | Path | None = None) -> Path:
        return write_html_report(self.run_dir, output)

    def summary(self) -> JsonDict:
        return summarize_run(self.run_dir)

    def _append_event(self, payload: JsonDict) -> None:
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, sort_keys=True))
            file.write("\n")

    def _copy_frame_image(self, image_path: str | Path, index: int) -> str:
        source = Path(image_path)
        suffix = source.suffix or ".bmp"
        target = self.frames_dir / f"frame_{index:06d}{suffix}"
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return str(target.relative_to(self.run_dir))

    def _write_manifest(self) -> None:
        payload = {
            "run": self.run_name,
            "created_at": time(),
            "metadata": self.metadata,
            "events": self.events_path.name,
            "frames": self.frames_dir.name,
        }
        self.manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_events(path: str | Path) -> list[JsonDict]:
    events = []
    event_path = Path(path)
    if not event_path.exists():
        return events
    with event_path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                events.append(json.loads(line))
    return events


def resolve_run_name(root: str | Path, run_name: str | None = "auto") -> str:
    if run_name is not None:
        cleaned = str(run_name).strip()
        if cleaned and cleaned.lower() not in {"auto", "next"}:
            return cleaned
    return next_numeric_run_name(root)


def next_numeric_run_name(root: str | Path) -> str:
    root_path = Path(root)
    max_seen = 0
    if root_path.exists():
        for child in root_path.iterdir():
            if child.is_dir() and child.name.isdigit():
                max_seen = max(max_seen, int(child.name))
    return str(max_seen + 1)


def summarize_run(run_dir: str | Path) -> JsonDict:
    root = Path(run_dir)
    manifest = _load_manifest(root)
    events = load_events(root / "events.jsonl")
    frames = [event for event in events if event.get("type") == "frame"]
    event_counts: dict[str, int] = {}
    for event in events:
        if event.get("type") == "event":
            kind = str(event.get("kind", "unknown"))
            event_counts[kind] = event_counts.get(kind, 0) + 1
    return {
        "run": manifest.get("run", root.name),
        "run_dir": str(root.resolve()),
        "frames": len(frames),
        "events": len(events),
        "event_counts": event_counts,
        "metadata": manifest.get("metadata", {}),
    }


def write_html_report(run_dir: str | Path, output: str | Path | None = None) -> Path:
    root = Path(run_dir)
    output_path = Path(output) if output else root / "report.html"
    manifest = _load_manifest(root)
    run_name = str(manifest.get("run", root.name))
    events = load_events(root / "events.jsonl")
    frames = [event for event in events if event.get("type") == "frame"]
    rows = "\n".join(_frame_row(frame) for frame in frames)
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AlienHand Telemetry - {escape(run_name)}</title>
<style>
body {{ background: #11151c; color: #e7edf5; font-family: Consolas, monospace; margin: 24px; }}
article {{ border: 1px solid #344052; border-radius: 10px; margin: 16px 0; padding: 14px; background: #18202b; }}
img {{ max-width: 360px; image-rendering: pixelated; border: 1px solid #536173; }}
pre {{ white-space: pre-wrap; background: #0d1117; padding: 10px; border-radius: 8px; overflow: auto; }}
.meta {{ color: #9fb0c2; }}
</style>
</head>
<body>
<h1>AlienHand Telemetry: {escape(run_name)}</h1>
<p class="meta">Frames: {len(frames)} | Events: {len(events)}</p>
{rows}
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _load_manifest(run_dir: Path) -> JsonDict:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _frame_row(frame: JsonDict) -> str:
    image = frame.get("image")
    image_html = f'<img src="{escape(str(image))}" alt="frame {frame.get("index")}">' if image else "<p>No image</p>"
    payload = {
        "observation": frame.get("observation", {}),
        "state": frame.get("state", {}),
        "delta": frame.get("delta", {}),
        "variables": frame.get("variables", {}),
        "resources": frame.get("resources", {}),
        "decision": frame.get("decision"),
        "action": frame.get("action"),
        "debug": frame.get("debug", {}),
    }
    return f"""<article>
<h2>Frame {frame.get("index")}</h2>
{image_html}
<pre>{escape(json.dumps(payload, indent=2, sort_keys=True))}</pre>
</article>"""


def _decision_to_dict(decision: DecisionTrace | JsonDict) -> JsonDict:
    if isinstance(decision, DecisionTrace):
        return asdict(decision)
    return decision
