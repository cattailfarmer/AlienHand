from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import sleep

from alienhand_ai.bitmap import load_bmp
from alienhand_ai.input_control import ClickAction, guarded_click
from alienhand_ai.learning import get_learning_profile, list_learning_profiles
from alienhand_ai.resources import ResourceMonitor
from alienhand_ai.telemetry import DecisionStep, DecisionTrace, TelemetryRecorder, summarize_run, write_html_report
from alienhand_ai.vision.tracking import ObjectTracker, detect_foreground, detect_motion, scene_feature_matrix
from alienhand_ai.vision.visual_reflex import choose_visual_reflex
from alienhand_ai.window_capture import capture_focused_window

from .board import Board, Cell, GridSpec, read_board
from .solver import suggest_moves


def main() -> None:
    parser = argparse.ArgumentParser(prog="minesweeper-ai")
    sub = parser.add_subparsers(dest="command", required=True)

    observe = sub.add_parser("observe")
    observe.add_argument("--output", default="observation.bmp")

    profiles = sub.add_parser("profiles")

    suggest = sub.add_parser("suggest")
    suggest.add_argument("--bmp", required=True)
    suggest.add_argument("--rows", type=int, required=True)
    suggest.add_argument("--cols", type=int, required=True)
    suggest.add_argument("--left", type=int, default=0)
    suggest.add_argument("--top", type=int, default=0)
    suggest.add_argument("--cell-size", type=int, default=16)

    act = sub.add_parser("act")
    act.add_argument("--row", type=int, required=True)
    act.add_argument("--col", type=int, required=True)
    act.add_argument("--left", type=int, required=True)
    act.add_argument("--top", type=int, required=True)
    act.add_argument("--cell-size", type=int, required=True)
    act.add_argument("--button", choices=["left", "right"], default="left")
    act.add_argument("--title-contains", required=True)
    act.add_argument("--allow-input", action="store_true")

    record = sub.add_parser("record")
    record.add_argument("--output", default="runs")
    record.add_argument("--name", default="minesweeper")
    record.add_argument("--frames", type=int, default=60)
    record.add_argument("--interval", type=float, default=0.2)
    record.add_argument("--profile", default="observer")
    record.add_argument("--rows", type=int)
    record.add_argument("--cols", type=int)
    record.add_argument("--left", type=int, default=0)
    record.add_argument("--top", type=int, default=0)
    record.add_argument("--cell-size", type=int, default=16)
    record.add_argument("--report", action="store_true")
    record.add_argument("--no-resource-metrics", action="store_true")
    record.add_argument("--track-objects", action="store_true")
    record.add_argument("--tracking-source", choices=["foreground", "motion"], default="foreground")
    record.add_argument("--foreground-threshold", type=int, default=32)
    record.add_argument("--motion-threshold", type=int, default=40)
    record.add_argument("--min-object-area", type=int, default=4)

    report = sub.add_parser("report")
    report.add_argument("--run", required=True)
    report.add_argument("--output")

    args = parser.parse_args()
    if args.command == "observe":
        info = capture_focused_window(args.output)
        print(json.dumps({"title": info.title, "process_id": info.process_id, "output": str(Path(args.output).resolve()), "width": info.width, "height": info.height}, indent=2))
    elif args.command == "profiles":
        print(json.dumps([profile.to_dict() for profile in list_learning_profiles()], indent=2))
    elif args.command == "suggest":
        bitmap = load_bmp(args.bmp)
        spec = GridSpec(left=args.left, top=args.top, cell_size=args.cell_size, rows=args.rows, cols=args.cols)
        board = read_board(bitmap, spec)
        moves = suggest_moves(board)
        print(json.dumps([move.__dict__ for move in moves], indent=2))
    elif args.command == "act":
        spec = GridSpec(left=args.left, top=args.top, cell_size=args.cell_size, rows=1, cols=1)
        x, y = spec.center(args.row, args.col)
        guarded_click(ClickAction(x=x, y=y, button=args.button), args.title_contains, args.allow_input)
    elif args.command == "record":
        profile = get_learning_profile(args.profile)
        recorder = TelemetryRecorder(
            args.output,
            args.name,
            metadata={
                "game": "minesweeper",
                "learning_profile": profile.to_dict(),
                "resource_metrics": not args.no_resource_metrics,
                "object_tracking": args.track_objects,
            },
        )
        recorder.log_event("recording_started", {"profile": profile.name, "frames": args.frames, "interval": args.interval})
        resource_monitor = None if args.no_resource_metrics else ResourceMonitor()
        tracker = ObjectTracker() if args.track_objects else None
        previous_bitmap = None
        spec = None
        if args.rows is not None and args.cols is not None:
            spec = GridSpec(left=args.left, top=args.top, cell_size=args.cell_size, rows=args.rows, cols=args.cols)
        for index in range(args.frames):
            capture_path = recorder.run_dir / f"capture_{index + 1:06d}.bmp"
            info = capture_focused_window(capture_path)
            resources = resource_monitor.sample(info.process_id, drive_path=capture_path).to_dict() if resource_monitor else {}
            observation = {"window": {"title": info.title, "process_id": info.process_id, "width": info.width, "height": info.height}}
            variables = {"frame": index + 1, "profile": profile.name}
            decision = None
            action = None
            bitmap = None
            if spec is not None or tracker is not None:
                bitmap = load_bmp(capture_path)
            if tracker is not None and bitmap is not None:
                if args.tracking_source == "motion" and previous_bitmap is not None:
                    detections = detect_motion(previous_bitmap, bitmap, threshold=args.motion_threshold, min_area=args.min_object_area)
                else:
                    detections = detect_foreground(bitmap, threshold=args.foreground_threshold, min_area=args.min_object_area)
                scene = tracker.update(detections)
                reflex = choose_visual_reflex(scene.tracks)
                observation["visual_scene"] = scene.to_dict()
                variables["track_count"] = len(scene.tracks)
                variables["detection_count"] = len(scene.detections)
                variables["object_features"] = scene_feature_matrix(scene.tracks, bitmap.width, bitmap.height, max_tracks=16)
                if decision is None:
                    decision = DecisionTrace(
                        policy="visual_reflex_tracker",
                        steps=(
                            DecisionStep(
                                label="track_objects",
                                output={"detections": len(scene.detections), "tracks": len(scene.tracks)},
                                confidence=0.5,
                                reason="Foreground/motion connected components matched across frames.",
                            ),
                            DecisionStep(
                                label="choose_visual_reflex",
                                output=reflex.to_dict(),
                                confidence=reflex.urgency,
                                reason="Classical reflex cue feeding future object-sequence transformer policy.",
                            ),
                        ),
                        selected_action={"type": "attend", **reflex.to_dict()},
                    )
                previous_bitmap = bitmap
            if spec is not None and bitmap is not None:
                board = read_board(bitmap, spec)
                moves = suggest_moves(board)
                observation["board"] = _board_to_json(board)
                variables["cell_counts"] = _cell_counts(board)
                action = moves[0].__dict__ if moves else None
                decision = DecisionTrace(
                    policy="minesweeper_rule_baseline",
                    steps=(
                        DecisionStep(
                            label="read_board",
                            output={"rows": board.rows, "cols": board.cols, "cell_counts": variables["cell_counts"]},
                            confidence=0.5,
                            reason="Pixel-threshold board classifier.",
                        ),
                        DecisionStep(
                            label="suggest_moves",
                            output={"move_count": len(moves), "first_move": action},
                            confidence=0.75 if moves else 0.0,
                            reason="Deterministic Minesweeper constraint rules.",
                        ),
                    ),
                    selected_action=action,
                )
            recorder.record_frame(
                image_path=capture_path,
                observation=observation,
                variables=variables,
                resources=resources,
                decision=decision,
                action=action,
                debug={"source_capture": str(capture_path.name)},
            )
            capture_path.unlink(missing_ok=True)
            if index + 1 < args.frames:
                sleep(args.interval)
        recorder.log_event("recording_finished", recorder.summary(), frame_index=recorder.frame_index)
        report_path = recorder.write_report() if args.report else None
        print(json.dumps({"summary": recorder.summary(), "report": str(report_path) if report_path else None}, indent=2))
    elif args.command == "report":
        report_path = write_html_report(args.run, args.output)
        print(json.dumps({"summary": summarize_run(args.run), "report": str(report_path.resolve())}, indent=2))


def _board_to_json(board: Board) -> dict[str, object]:
    return {
        "rows": board.rows,
        "cols": board.cols,
        "cells": [cell.value for cell in board.cells],
    }


def _cell_counts(board: Board) -> dict[str, int]:
    counts: dict[str, int] = {}
    for cell in board.cells:
        key = cell.value if isinstance(cell, Cell) else str(cell)
        counts[key] = counts.get(key, 0) + 1
    return counts


if __name__ == "__main__":
    main()
