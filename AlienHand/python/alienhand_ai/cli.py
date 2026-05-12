from __future__ import annotations

import argparse
import json
from pathlib import Path

from .godot import load_godot_events, summarize_godot_events
from .interface_probe import probe_godot_interfaces, summarize_interface_map
from .interaction_trace import summarize_interaction_trace, write_interaction_trace
from .learning_memory import ingest_learning_digest, summarize_learning_memory
from .learning_packets import summarize_learning_packets, write_learning_packets
from .runner import ProcessRunConfig, record_process
from .sop import compile_sop_file, compile_sop_text, write_sop_packets
from .telemetry import TelemetryRecorder, summarize_run, write_html_report


def main() -> None:
    parser = argparse.ArgumentParser(prog="alienhand")
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record-process")
    record.add_argument("--output", default="runs")
    record.add_argument("--name", default="auto")
    record.add_argument("--cwd")
    record.add_argument("--frames", type=int, default=120)
    record.add_argument("--interval", type=float, default=0.25)
    record.add_argument("--title-contains")
    record.add_argument("--no-window-capture", action="store_true")
    record.add_argument("--no-frame-images", action="store_true")
    record.add_argument("--no-resource-metrics", action="store_true")
    record.add_argument("--track-objects", action="store_true")
    record.add_argument("--observe-input", action="store_true")
    record.add_argument("--no-observe-mouse-move", action="store_true")
    record.add_argument("--raw-mouse-motion", action="store_true")
    record.add_argument("--motion-window-ms", type=float, default=100.0)
    record.add_argument("--motion-tolerance", type=float, default=0.2)
    record.add_argument("--watch-title-only", action="store_true")
    record.add_argument("--no-report", action="store_true")
    record.add_argument("--terminate-on-finish", action="store_true")
    record.add_argument("command_args", nargs=argparse.REMAINDER)

    godot_summary = sub.add_parser("godot-summary")
    godot_summary.add_argument("--log", required=True)

    godot_import = sub.add_parser("godot-import")
    godot_import.add_argument("--log", required=True)
    godot_import.add_argument("--output", default="runs")
    godot_import.add_argument("--name", default="auto")
    godot_import.add_argument("--report", action="store_true")

    report = sub.add_parser("report")
    report.add_argument("--run", required=True)
    report.add_argument("--output")

    interactions = sub.add_parser("interaction-trace")
    interactions.add_argument("--run", required=True)
    interactions.add_argument("--output")

    packets = sub.add_parser("learning-packets")
    packets.add_argument("--run", required=True)
    packets.add_argument("--output")

    sop = sub.add_parser("sop-compile")
    sop.add_argument("--file")
    sop.add_argument("--text")
    sop.add_argument("--name", default="sop")
    sop.add_argument("--output")
    sop.add_argument("--packets-output")

    ingest = sub.add_parser("ingest-learning-digest")
    ingest.add_argument("--packets", required=True)
    ingest.add_argument("--digest", required=True)
    ingest.add_argument("--output", required=True)
    ingest.add_argument("--existing-memory")

    probe = sub.add_parser("probe-godot")
    probe.add_argument("--root", required=True)
    probe.add_argument("--output")

    args = parser.parse_args()
    if args.command == "record-process":
        command = _normalize_remainder(args.command_args)
        if not command:
            raise SystemExit("record-process requires a command after --")
        if args.watch_title_only and not args.title_contains:
            raise SystemExit("--watch-title-only requires --title-contains")
        result = record_process(
            ProcessRunConfig(
                command=command,
                output=args.output,
                name=args.name,
                cwd=args.cwd,
                frames=args.frames,
                interval=args.interval,
                capture_window=not args.no_window_capture,
                capture_images=not args.no_frame_images,
                title_contains=args.title_contains,
                resource_metrics=not args.no_resource_metrics,
                track_objects=args.track_objects,
                observe_input=args.observe_input,
                observe_mouse_move=not args.no_observe_mouse_move,
                compress_mouse_motion=not args.raw_mouse_motion,
                motion_window_ms=args.motion_window_ms,
                motion_tolerance=args.motion_tolerance,
                watch_title_only=args.watch_title_only,
                report=not args.no_report,
                terminate_on_finish=args.terminate_on_finish,
            )
        )
        print(json.dumps(result, indent=2))
    elif args.command == "godot-summary":
        events = load_godot_events(args.log)
        print(json.dumps(summarize_godot_events(events), indent=2))
    elif args.command == "godot-import":
        result = import_godot_log(args.log, args.output, args.name, args.report)
        print(json.dumps(result, indent=2))
    elif args.command == "report":
        report_path = write_html_report(args.run, args.output)
        print(json.dumps({"summary": summarize_run(args.run), "report": str(report_path.resolve())}, indent=2))
    elif args.command == "interaction-trace":
        output = write_interaction_trace(args.run, args.output)
        print(json.dumps({"summary": summarize_interaction_trace(args.run), "output": str(output.resolve())}, indent=2))
    elif args.command == "learning-packets":
        output = write_learning_packets(args.run, args.output)
        print(json.dumps({"summary": summarize_learning_packets(args.run), "output": str(output.resolve())}, indent=2))
    elif args.command == "sop-compile":
        if bool(args.file) == bool(args.text):
            raise SystemExit("sop-compile requires exactly one of --file or --text")
        program = compile_sop_file(args.file, name=args.name) if args.file else compile_sop_text(args.text, name=args.name)
        output = program.write_json(args.output) if args.output else None
        packets_output = write_sop_packets(program, args.packets_output) if args.packets_output else None
        print(
            json.dumps(
                {
                    "summary": program.to_dict()["summary"],
                    "output": str(output.resolve()) if output else None,
                    "packets_output": str(packets_output.resolve()) if packets_output else None,
                },
                indent=2,
            )
        )
    elif args.command == "ingest-learning-digest":
        memory = ingest_learning_digest(args.packets, args.digest, args.output, existing_memory=args.existing_memory)
        print(json.dumps({"summary": summarize_learning_memory(memory), "output": str(Path(args.output).resolve())}, indent=2))
    elif args.command == "probe-godot":
        interface_map = probe_godot_interfaces(args.root)
        output = interface_map.write_json(args.output) if args.output else None
        print(json.dumps({"summary": summarize_interface_map(interface_map), "output": str(output) if output else None}, indent=2))


def import_godot_log(log_path: str | Path, output: str | Path, name: str, report: bool) -> dict[str, object]:
    events = load_godot_events(log_path)
    recorder = TelemetryRecorder(output, name, metadata={"kind": "godot_import", "source": str(log_path)})
    for event in events:
        if event.type == "godot_frame":
            recorder.record_frame(
                observation={"godot": event.payload},
                variables={
                    "session_id": event.session_id,
                    "godot_frame_index": event.frame_index,
                    "ticks_msec": event.ticks_msec,
                    "unix_time": event.unix_time,
                },
            )
        else:
            recorder.log_event(event.type, event.to_dict(), frame_index=recorder.frame_index)
    report_path = recorder.write_report() if report else None
    return {"summary": recorder.summary(), "godot": summarize_godot_events(events), "report": str(report_path) if report_path else None}


def _normalize_remainder(values: list[str]) -> list[str]:
    if values and values[0] == "--":
        return values[1:]
    return values


if __name__ == "__main__":
    main()
