from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from time import sleep, time

from .bitmap import load_bmp
from .input_observer import InputObserver, InputObserverConfig
from .motion import MotionCompressionConfig, compress_input_events
from .resources import ResourceMonitor
from .telemetry import JsonDict, TelemetryRecorder
from .vision.tracking import ObjectTracker, detect_foreground, scene_feature_matrix, summarize_track_group_motion
from .vision.visual_reflex import choose_visual_reflex
from .window_capture import capture_window, find_window, window_info_from_handle


@dataclass(frozen=True)
class ProcessRunConfig:
    command: list[str]
    output: str | Path = "runs"
    name: str | None = "auto"
    cwd: str | Path | None = None
    frames: int = 120
    interval: float = 0.25
    capture_window: bool = True
    capture_images: bool = True
    title_contains: str | None = None
    resource_metrics: bool = True
    track_objects: bool = False
    observe_input: bool = False
    observe_mouse_move: bool = True
    compress_mouse_motion: bool = True
    motion_window_ms: float = 100.0
    motion_tolerance: float = 0.2
    watch_title_only: bool = False
    report: bool = True
    terminate_on_finish: bool = False


def record_process(config: ProcessRunConfig) -> JsonDict:
    recorder = TelemetryRecorder(
        config.output,
        config.name,
        metadata={
            "kind": "process_run",
            "command": config.command,
            "cwd": str(config.cwd) if config.cwd else None,
            "capture_window": config.capture_window,
            "capture_images": config.capture_images,
            "resource_metrics": config.resource_metrics,
            "track_objects": config.track_objects,
            "observe_input": config.observe_input,
            "observe_mouse_move": config.observe_mouse_move,
            "compress_mouse_motion": config.compress_mouse_motion,
            "motion_window_ms": config.motion_window_ms,
            "motion_tolerance": config.motion_tolerance,
            "watch_title_only": config.watch_title_only,
        },
    )
    stdout_path = recorder.run_dir / "stdout.log"
    stderr_path = recorder.run_dir / "stderr.log"
    resource_monitor = ResourceMonitor() if config.resource_metrics else None
    tracker = ObjectTracker() if config.track_objects else None
    input_observer: InputObserver | None = None
    completed_frame_limit = False

    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(config.command, cwd=config.cwd, stdout=stdout, stderr=stderr)
        start_time = time()
        recorder.log_event("process_started", {"pid": process.pid, "command": config.command})
        try:
            if config.observe_input:
                try:
                    input_observer = InputObserver(
                        InputObserverConfig(
                            process_id=None if config.watch_title_only else process.pid,
                            title_contains=config.title_contains,
                            include_mouse_move=config.observe_mouse_move,
                        )
                    )
                    input_observer.start()
                    recorder.log_event(
                        "input_observer_started",
                        {
                            "pid": process.pid,
                            "title_contains": config.title_contains,
                            "include_mouse_move": config.observe_mouse_move,
                        },
                    )
                except RuntimeError as exc:
                    recorder.log_event("input_observer_error", {"message": str(exc)})

            window = _find_target_window(config, process.pid, timeout=5.0) if config.capture_window else None
            if window is not None:
                recorder.log_event("window_found", {"hwnd": window.hwnd, "title": window.title, "process_id": window.process_id})
            elif config.capture_window:
                recorder.log_event("window_not_found", {"pid": process.pid, "title_contains": config.title_contains})

            for index in range(config.frames):
                if process.poll() is not None:
                    recorder.log_event("process_exited", {"returncode": process.returncode}, frame_index=recorder.frame_index)
                    break

                image_path = None
                observation: JsonDict = {"process": {"pid": process.pid, "running": True}}
                variables: JsonDict = {"frame": index + 1, "elapsed_seconds": time() - start_time}
                input_events: list[JsonDict] = []
                debug: JsonDict = {"stdout": stdout_path.name, "stderr": stderr_path.name}

                if config.capture_window:
                    if window is None:
                        window = _find_target_window(config, process.pid, timeout=0.0)
                    if window is not None:
                        if config.capture_images:
                            capture_path = recorder.run_dir / f"capture_{index + 1:06d}.bmp"
                            window = capture_window(window.hwnd, capture_path)
                            image_path = capture_path
                        else:
                            try:
                                window = window_info_from_handle(window.hwnd)
                            except OSError:
                                window = _find_target_window(config, process.pid, timeout=0.0)
                                if window is None:
                                    debug["window"] = "lost"
                                    window = None
                    if window is not None:
                        observation["window"] = {
                            "hwnd": window.hwnd,
                            "title": window.title,
                            "process_id": window.process_id,
                            "width": window.width,
                            "height": window.height,
                        }

                if tracker is not None and image_path is not None:
                    bitmap = load_bmp(image_path)
                    detections = detect_foreground(bitmap)
                    scene = tracker.update(detections)
                    reflex = choose_visual_reflex(scene.tracks)
                    visual_scene = scene.to_dict()
                    visual_scene["group_motion"] = summarize_track_group_motion(scene.tracks)
                    observation["visual_scene"] = visual_scene
                    variables["track_count"] = len(scene.tracks)
                    variables["object_features"] = scene_feature_matrix(scene.tracks, bitmap.width, bitmap.height, max_tracks=16)
                    variables["visual_reflex"] = reflex.to_dict()
                elif tracker is not None and not config.capture_images:
                    debug["visual_tracking"] = "disabled_without_frame_images"

                if input_observer is not None:
                    raw_input_events = [event.to_dict() for event in input_observer.drain()]
                    input_events = _compress_input_events(raw_input_events, config)
                    if input_events:
                        observation["input_events"] = input_events
                        variables["input_event_count"] = len(input_events)
                        variables["raw_input_event_count"] = len(raw_input_events)
                        for event in input_events:
                            recorder.log_event("observed_input", event, frame_index=recorder.frame_index)

                resources = resource_monitor.sample(process.pid, drive_path=recorder.run_dir).to_dict() if resource_monitor else {}
                state = _observation_state(
                    observation=observation,
                    variables=variables,
                    resources=resources,
                    input_event_count=len(input_events),
                )
                recorder.record_frame(
                    image_path=image_path,
                    state=state,
                    observation=observation,
                    variables=variables,
                    resources=resources,
                    debug=debug,
                )
                if image_path is not None:
                    Path(image_path).unlink(missing_ok=True)
                if index + 1 < config.frames:
                    sleep(config.interval)
            else:
                completed_frame_limit = True
        finally:
            if input_observer is not None:
                raw_remaining = [event.to_dict() for event in input_observer.drain()]
                remaining = _compress_input_events(raw_remaining, config)
                for event in remaining:
                    recorder.log_event("observed_input", event, frame_index=recorder.frame_index)
                input_observer.stop()
                recorder.log_event(
                    "input_observer_stopped",
                    {"remaining_events": len(remaining), "raw_remaining_events": len(raw_remaining)},
                    frame_index=recorder.frame_index,
                )
            if completed_frame_limit and process.poll() is None:
                recorder.log_event("frame_limit_reached", {"frames": config.frames, "pid": process.pid, "process_running": True}, frame_index=recorder.frame_index)
            if config.terminate_on_finish and process.poll() is None:
                process.terminate()
                recorder.log_event("process_terminated", {"pid": process.pid}, frame_index=recorder.frame_index)
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            elif process.poll() is not None:
                process.wait(timeout=1.0)
    report_path = recorder.write_report() if config.report else None
    return {"summary": recorder.summary(), "report": str(report_path) if report_path else None}


def _observation_state(observation: JsonDict, variables: JsonDict, resources: JsonDict, input_event_count: int) -> JsonDict:
    state: JsonDict = {
        "process": observation.get("process", {}),
        "window": observation.get("window", {}),
        "inputs": {
            "count": input_event_count,
            "raw_count": variables.get("raw_input_event_count", input_event_count),
            "compressed": bool(variables.get("raw_input_event_count", input_event_count) != input_event_count),
        },
        "timing": {
            "frame": variables.get("frame"),
            "elapsed_seconds": variables.get("elapsed_seconds"),
        },
    }
    if "visual_scene" in observation:
        tracks = observation["visual_scene"].get("tracks", []) if isinstance(observation["visual_scene"], dict) else []
        state["visual"] = {"track_count": len(tracks)}
    if resources:
        process_resources = resources.get("process", {}) if isinstance(resources, dict) else {}
        memory = process_resources.get("memory", {}) if isinstance(process_resources.get("memory"), dict) else {}
        io = process_resources.get("io", {}) if isinstance(process_resources.get("io"), dict) else {}
        state["resources"] = {
            "cpu_percent": process_resources.get("cpu_percent_since_last_sample"),
            "memory_working_set": memory.get("working_set_bytes"),
            "read_bytes": io.get("read_transfer_bytes"),
            "write_bytes": io.get("write_transfer_bytes"),
        }
    return state


def _find_target_window(config: ProcessRunConfig, process_id: int, timeout: float) -> object:
    if config.watch_title_only:
        return find_window(timeout=timeout, title_contains=config.title_contains)
    return find_window(timeout=timeout, process_id=process_id, title_contains=config.title_contains)


def _compress_input_events(input_events: list[JsonDict], config: ProcessRunConfig) -> list[JsonDict]:
    if not config.compress_mouse_motion:
        return input_events
    return compress_input_events(
        input_events,
        MotionCompressionConfig(
            window_ms=config.motion_window_ms,
            tolerance_ratio=config.motion_tolerance,
        ),
    )
