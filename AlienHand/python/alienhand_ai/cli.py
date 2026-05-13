from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import sleep
from uuid import uuid4

from .chat_platform import (
    AlienHandChatService,
    ChannelJSONLHistory,
    IRCNetworkPublisher,
    PayloadStore,
    commit_message,
    irc_channel_name,
    run_app_refinement_lifecycle_proof,
    run_app_lifecycle_proof,
    run_chat_truth_test,
    run_ergo_lifecycle_proof,
    run_history_replay_proof,
    run_render_model_proof,
    run_user_client_proof,
)
from .godot import load_godot_events, summarize_godot_events
from .interface_probe import probe_godot_interfaces, summarize_interface_map
from .interaction_trace import summarize_interaction_trace, write_interaction_trace
from .learning_memory import ingest_learning_digest, summarize_learning_memory
from .learning_packets import summarize_learning_packets, write_learning_packets
from .multi_mouse import (
    run_multi_mouse_assignment_suggestion,
    run_multi_mouse_capture_preflight,
    run_multi_mouse_device_scan,
    run_multi_mouse_pipeline_proof,
    run_multi_mouse_virtualization_proof,
)
from .payload_http import (
    run_app_payload_resolver_lifecycle_proof,
    run_app_thelounge_refinement_workbench_proof,
    run_app_thelounge_runtime_group_proof,
    run_payload_resolver_http_proof,
    run_refinement_http_api_proof,
)
from .refinement_storage import run_refinement_replay_import_proof, run_refinement_storage_proof
from .runner import ProcessRunConfig, record_process
from .sop import compile_sop_file, compile_sop_text, write_sop_packets
from .telemetry import TelemetryRecorder, summarize_run, write_html_report
from .thelounge_adapter import run_thelounge_adapter_proof


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

    chat_truth = sub.add_parser("chat-truth-test")
    chat_truth.add_argument("--output", default="runs")
    chat_truth.add_argument("--name", default="chat-truth-test")

    chat_irc = sub.add_parser("chat-irc-publish")
    chat_irc.add_argument("--host", default="127.0.0.1")
    chat_irc.add_argument("--port", type=int, required=True)
    chat_irc.add_argument("--nick", default="alienhand-agent")
    chat_irc.add_argument("--app-id", type=int, default=1)
    chat_irc.add_argument("--channel")
    chat_irc.add_argument("--text", default="hello AlienHand")
    chat_irc.add_argument("--output", default="runs")
    chat_irc.add_argument("--name", default="chat-irc-publish")

    chat_ergo = sub.add_parser("chat-ergo-proof")
    chat_ergo.add_argument("--ergo-root")
    chat_ergo.add_argument("--port", type=int)
    chat_ergo.add_argument("--nick", default="alienhandagent")
    chat_ergo.add_argument("--app-id", type=int, default=1)
    chat_ergo.add_argument("--text", default="hello Ergo")
    chat_ergo.add_argument("--output", default="runs")
    chat_ergo.add_argument("--name", default="chat-ergo-proof")

    chat_app = sub.add_parser("chat-app-lifecycle-proof")
    chat_app.add_argument("--ergo-root")
    chat_app.add_argument("--port", type=int)
    chat_app.add_argument("--nick", default="alienhandagent")
    chat_app.add_argument("--app-id", type=int, default=1)
    chat_app.add_argument("--text", default="hello app-owned Ergo")
    chat_app.add_argument("--output", default="runs")
    chat_app.add_argument("--name", default="chat-app-lifecycle-proof")

    chat_app_refinement = sub.add_parser("chat-app-refinement-proof")
    chat_app_refinement.add_argument("--app-id", type=int, default=1)
    chat_app_refinement.add_argument("--text", default="hello searchable refinement")
    chat_app_refinement.add_argument("--output", default="runs")
    chat_app_refinement.add_argument("--name", default="chat-app-refinement-proof")

    chat_user = sub.add_parser("chat-user-client-proof")
    chat_user.add_argument("--ergo-root")
    chat_user.add_argument("--port", type=int)
    chat_user.add_argument("--agent-nick", default="alienhandagent")
    chat_user.add_argument("--user-nick", default="alienhanduser")
    chat_user.add_argument("--app-id", type=int, default=1)
    chat_user.add_argument("--text", default="hello user client")
    chat_user.add_argument("--output", default="runs")
    chat_user.add_argument("--name", default="chat-user-client-proof")

    chat_history = sub.add_parser("chat-history-proof")
    chat_history.add_argument("--app-id", type=int, default=1)
    chat_history.add_argument("--messages", type=int, default=5)
    chat_history.add_argument("--chunk-size", type=int, default=2)
    chat_history.add_argument("--limit", type=int, default=4)
    chat_history.add_argument("--command", dest="history_command")
    chat_history.add_argument("--output", default="runs")
    chat_history.add_argument("--name", default="chat-history-proof")

    chat_render = sub.add_parser("chat-render-proof")
    chat_render.add_argument("--app-id", type=int, default=1)
    chat_render.add_argument("--output", default="runs")
    chat_render.add_argument("--name", default="chat-render-proof")

    chat_thelounge = sub.add_parser("chat-thelounge-adapter-proof")
    chat_thelounge.add_argument("--app-id", type=int, default=1)
    chat_thelounge.add_argument("--output", default="runs")
    chat_thelounge.add_argument("--name", default="chat-thelounge-adapter-proof")

    chat_payload_http = sub.add_parser("chat-payload-resolver-proof")
    chat_payload_http.add_argument("--app-id", type=int, default=1)
    chat_payload_http.add_argument("--output", default="runs")
    chat_payload_http.add_argument("--name", default="chat-payload-resolver-proof")

    chat_app_resolver = sub.add_parser("chat-app-resolver-proof")
    chat_app_resolver.add_argument("--ergo-root")
    chat_app_resolver.add_argument("--port", type=int)
    chat_app_resolver.add_argument("--nick", default="alienhandagent")
    chat_app_resolver.add_argument("--app-id", type=int, default=1)
    chat_app_resolver.add_argument("--text", default="hello app-owned payload resolver")
    chat_app_resolver.add_argument("--output", default="runs")
    chat_app_resolver.add_argument("--name", default="chat-app-resolver-proof")

    chat_runtime_group = sub.add_parser("chat-runtime-group-proof")
    chat_runtime_group.add_argument("--ergo-root")
    chat_runtime_group.add_argument("--thelounge-root")
    chat_runtime_group.add_argument("--port", type=int)
    chat_runtime_group.add_argument("--thelounge-port", type=int)
    chat_runtime_group.add_argument("--nick", default="alienhandagent")
    chat_runtime_group.add_argument("--app-id", type=int, default=1)
    chat_runtime_group.add_argument("--text", default="hello app-owned thelounge")
    chat_runtime_group.add_argument("--timeout", type=float, default=30.0)
    chat_runtime_group.add_argument("--output", default="runs")
    chat_runtime_group.add_argument("--name", default="chat-runtime-group-proof")

    chat_workbench_runtime = sub.add_parser("chat-workbench-runtime-proof")
    chat_workbench_runtime.add_argument("--ergo-root")
    chat_workbench_runtime.add_argument("--thelounge-root")
    chat_workbench_runtime.add_argument("--port", type=int)
    chat_workbench_runtime.add_argument("--thelounge-port", type=int)
    chat_workbench_runtime.add_argument("--nick", default="alienhandagent")
    chat_workbench_runtime.add_argument("--app-id", type=int, default=1)
    chat_workbench_runtime.add_argument("--text", default="hello app-owned refinement workbench")
    chat_workbench_runtime.add_argument("--timeout", type=float, default=30.0)
    chat_workbench_runtime.add_argument("--output", default="runs")
    chat_workbench_runtime.add_argument("--name", default="chat-workbench-runtime-proof")

    chat_workbench_live = sub.add_parser("chat-workbench-live")
    chat_workbench_live.add_argument("--ergo-root")
    chat_workbench_live.add_argument("--thelounge-root")
    chat_workbench_live.add_argument("--port", type=int)
    chat_workbench_live.add_argument("--thelounge-port", type=int)
    chat_workbench_live.add_argument("--nick", default="alienhandagent")
    chat_workbench_live.add_argument("--app-id", type=int, default=1)
    chat_workbench_live.add_argument("--channel")
    chat_workbench_live.add_argument("--text", default="hello live refinement workbench")
    chat_workbench_live.add_argument("--ready-file")
    chat_workbench_live.add_argument("--stop-file")
    chat_workbench_live.add_argument("--output", default="runs")
    chat_workbench_live.add_argument("--name", default="chat-workbench-live")

    chat_refinement_storage = sub.add_parser("chat-refinement-storage-proof")
    chat_refinement_storage.add_argument("--output", default="runs")
    chat_refinement_storage.add_argument("--name", default="chat-refinement-storage-proof")

    chat_refinement_replay = sub.add_parser("chat-refinement-replay-proof")
    chat_refinement_replay.add_argument("--app-id", type=int, default=1)
    chat_refinement_replay.add_argument("--output", default="runs")
    chat_refinement_replay.add_argument("--name", default="chat-refinement-replay-proof")

    chat_refinement_http = sub.add_parser("chat-refinement-http-proof")
    chat_refinement_http.add_argument("--app-id", type=int, default=1)
    chat_refinement_http.add_argument("--output", default="runs")
    chat_refinement_http.add_argument("--name", default="chat-refinement-http-proof")

    multi_mouse = sub.add_parser("multi-mouse-routing-proof")
    multi_mouse.add_argument("--output", default="runs")
    multi_mouse.add_argument("--name", default="multi-mouse-routing-proof")

    multi_mouse_scan = sub.add_parser("multi-mouse-device-scan")
    multi_mouse_scan.add_argument("--output", default="runs")
    multi_mouse_scan.add_argument("--name", default="multi-mouse-device-scan")

    multi_mouse_assign = sub.add_parser("multi-mouse-assignment-suggestion")
    multi_mouse_assign.add_argument("--output", default="runs")
    multi_mouse_assign.add_argument("--name", default="multi-mouse-assignment-suggestion")

    multi_mouse_capture = sub.add_parser("multi-mouse-capture-preflight")
    multi_mouse_capture.add_argument("--output", default="runs")
    multi_mouse_capture.add_argument("--name", default="multi-mouse-capture-preflight")

    multi_mouse_pipeline = sub.add_parser("multi-mouse-pipeline-proof")
    multi_mouse_pipeline.add_argument("--output", default="runs")
    multi_mouse_pipeline.add_argument("--name", default="multi-mouse-pipeline-proof")

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
    elif args.command == "chat-truth-test":
        result = run_chat_truth_test(Path(args.output) / args.name)
        print(json.dumps(result, indent=2))
    elif args.command == "chat-irc-publish":
        root = Path(args.output) / args.name
        channel_uuid = args.channel or uuid4().hex
        publisher = IRCNetworkPublisher(args.host, args.port, args.nick)
        try:
            published = commit_message(
                app_id=args.app_id,
                channel_uuid=channel_uuid,
                nick=args.nick,
                sender_type="ai_agent",
                payload_kind="text",
                content={"text": args.text},
                store=PayloadStore(root),
                history=ChannelJSONLHistory(root),
                publisher=publisher,
            )
        finally:
            publisher.close()
        print(
            json.dumps(
                {
                    "root": str(root.resolve()),
                    "channel_uuid": channel_uuid,
                    "message_uuid": published.envelope.message_uuid,
                    "envelope": published.envelope.to_line(),
                },
                indent=2,
            )
        )
    elif args.command == "chat-ergo-proof":
        result = run_ergo_lifecycle_proof(
            Path(args.output) / args.name,
            ergo_root=args.ergo_root,
            app_id=args.app_id,
            nick=args.nick,
            text=args.text,
            port=args.port,
        )
        print(json.dumps(result, indent=2))
    elif args.command == "chat-app-lifecycle-proof":
        result = run_app_lifecycle_proof(
            Path(args.output) / args.name,
            ergo_root=args.ergo_root,
            app_id=args.app_id,
            nick=args.nick,
            text=args.text,
            port=args.port,
        )
        print(json.dumps(result, indent=2))
    elif args.command == "chat-app-refinement-proof":
        result = run_app_refinement_lifecycle_proof(
            Path(args.output) / args.name,
            app_id=args.app_id,
            text=args.text,
        )
        print(json.dumps(result, indent=2))
    elif args.command == "chat-user-client-proof":
        result = run_user_client_proof(
            Path(args.output) / args.name,
            ergo_root=args.ergo_root,
            app_id=args.app_id,
            agent_nick=args.agent_nick,
            user_nick=args.user_nick,
            text=args.text,
            port=args.port,
        )
        print(json.dumps(result, indent=2))
    elif args.command == "chat-history-proof":
        result = run_history_replay_proof(
            Path(args.output) / args.name,
            app_id=args.app_id,
            message_count=args.messages,
            chunk_size=args.chunk_size,
            limit=args.limit,
            command_text=args.history_command,
        )
        print(json.dumps(result, indent=2))
    elif args.command == "chat-render-proof":
        result = run_render_model_proof(Path(args.output) / args.name, app_id=args.app_id)
        print(json.dumps(result, indent=2))
    elif args.command == "chat-thelounge-adapter-proof":
        result = run_thelounge_adapter_proof(Path(args.output) / args.name, app_id=args.app_id)
        print(json.dumps(result, indent=2))
    elif args.command == "chat-payload-resolver-proof":
        result = run_payload_resolver_http_proof(Path(args.output) / args.name, app_id=args.app_id)
        print(json.dumps(result, indent=2))
    elif args.command == "chat-app-resolver-proof":
        result = run_app_payload_resolver_lifecycle_proof(
            Path(args.output) / args.name,
            ergo_root=args.ergo_root,
            app_id=args.app_id,
            nick=args.nick,
            text=args.text,
            port=args.port,
        )
        print(json.dumps(result, indent=2))
    elif args.command == "chat-runtime-group-proof":
        result = run_app_thelounge_runtime_group_proof(
            Path(args.output) / args.name,
            ergo_root=args.ergo_root,
            thelounge_root=args.thelounge_root,
            app_id=args.app_id,
            nick=args.nick,
            text=args.text,
            port=args.port,
            thelounge_port=args.thelounge_port,
            timeout=args.timeout,
        )
        print(json.dumps(result, indent=2))
    elif args.command == "chat-workbench-runtime-proof":
        result = run_app_thelounge_refinement_workbench_proof(
            Path(args.output) / args.name,
            ergo_root=args.ergo_root,
            thelounge_root=args.thelounge_root,
            app_id=args.app_id,
            nick=args.nick,
            text=args.text,
            port=args.port,
            thelounge_port=args.thelounge_port,
            timeout=args.timeout,
        )
        print(json.dumps(result, indent=2))
    elif args.command == "chat-workbench-live":
        result = run_workbench_live_command(args)
        print(json.dumps(result, indent=2))
    elif args.command == "chat-refinement-storage-proof":
        result = run_refinement_storage_proof(Path(args.output) / args.name)
        print(json.dumps(result, indent=2))
    elif args.command == "chat-refinement-replay-proof":
        result = run_refinement_replay_import_proof(Path(args.output) / args.name, app_id=args.app_id)
        print(json.dumps(result, indent=2))
    elif args.command == "chat-refinement-http-proof":
        result = run_refinement_http_api_proof(Path(args.output) / args.name, app_id=args.app_id)
        print(json.dumps(result, indent=2))
    elif args.command == "multi-mouse-routing-proof":
        result = run_multi_mouse_virtualization_proof(Path(args.output) / args.name)
        print(json.dumps(result, indent=2))
    elif args.command == "multi-mouse-device-scan":
        result = run_multi_mouse_device_scan(Path(args.output) / args.name)
        print(json.dumps(result, indent=2))
    elif args.command == "multi-mouse-assignment-suggestion":
        result = run_multi_mouse_assignment_suggestion(Path(args.output) / args.name)
        print(json.dumps(result, indent=2))
    elif args.command == "multi-mouse-capture-preflight":
        result = run_multi_mouse_capture_preflight(Path(args.output) / args.name)
        print(json.dumps(result, indent=2))
    elif args.command == "multi-mouse-pipeline-proof":
        result = run_multi_mouse_pipeline_proof(Path(args.output) / args.name)
        print(json.dumps(result, indent=2))


def run_workbench_live_command(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.output) / args.name
    channel_uuid = args.channel or uuid4().hex
    ready_file = Path(args.ready_file) if args.ready_file else root / "workbench-live-ready.json"
    stop_file = Path(args.stop_file) if args.stop_file else None
    with AlienHandChatService(
        root,
        ergo_root=args.ergo_root,
        thelounge_root=args.thelounge_root,
        app_id=args.app_id,
        nick=args.nick,
        port=args.port,
        thelounge_port=args.thelounge_port,
        start_thelounge=True,
    ) as service:
        published = service.publish_text(
            args.text,
            channel_uuid=channel_uuid,
            metadata={"proof": "workbench_live_review"},
        )
        ready = {
            "root": str(root.resolve()),
            "app_id": args.app_id,
            "channel_uuid": channel_uuid,
            "irc_channel": irc_channel_name(channel_uuid),
            "message_uuid": published.envelope.message_uuid,
            "ergo_port": service.port,
            "resolver_base_url": service.payload_resolver_base_url,
            "thelounge_base_url": service.thelounge_base_url,
            "ready_file": str(ready_file.resolve()),
            "stop_file": str(stop_file.resolve()) if stop_file else None,
        }
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        ready_file.write_text(json.dumps(ready, indent=2), encoding="utf-8")
        print(json.dumps({"ready": ready}, indent=2), flush=True)
        try:
            if stop_file is None:
                while True:
                    sleep(0.5)
            else:
                while not stop_file.exists():
                    sleep(0.5)
        except KeyboardInterrupt:
            pass
    return {
        "root": str(root.resolve()),
        "ready_file": str(ready_file.resolve()),
        "stop_file": str(stop_file.resolve()) if stop_file else None,
        "workbench_live_stopped": True,
    }


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
