from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .chat_platform import (
    ChannelJSONLHistory,
    OLDEST_FIRST_RECONSTRUCT,
    PayloadResolver,
    PayloadStore,
    RECENT_FIRST_BACKFILL,
    normalize_channel_uuid,
    payload_error,
    utc_timestamp_ms,
)


def _supported_task_types() -> dict[str, str]:
    return {
        "sop_compile": "SOP compile is routed to manual review in the prototype boundary.",
        "route_decision": "Routing decision requires external policy hooks and is not auto-executed.",
        "llm_decision": "LLM decision is intentionally unimplemented pending live model handoff.",
        "history_context_pack": "History context pack preparation is currently delegated to upstream runtime.",
        "spec_explosion": "Spec explosion is staged for dedicated processor integration.",
        "justification_record": "Justification recording is a manual/provenance control-plane concern.",
        "tool_handoff": "Tool handoff requires authenticated external handoff target.",
        "health_check": "health_check is fully handled in the streaming frontier.",
    }


def execute_stubbed_task(request: dict[str, Any], *, fallback_status: str = "needs_human") -> dict[str, Any]:
    task_type = str(request.get("task_type", ""))
    if task_type == "health_check":
        return {
            "status": "completed",
            "result_summary": "health check stub executed",
            "result_ref": None,
            "artifact_refs": [],
            "retryable": False,
        }
    if task_type in _supported_task_types():
        return {
            "status": fallback_status,
            "result_summary": _supported_task_types()[task_type],
            "error_ref": f"stubbed_task:{task_type}",
            "retryable": False,
            "artifact_refs": [],
        }
    return {
        "status": "needs_human",
        "result_summary": f"unsupported stub task_type: {task_type}",
        "error_ref": "stubbed_task:unsupported",
        "retryable": False,
        "artifact_refs": [],
    }


def execute_alienhand_task(
    request: dict[str, Any],
    *,
    root: str | Path | None = None,
    fallback_status: str = "needs_human",
) -> dict[str, Any]:
    task_type = str(request.get("task_type", ""))
    if task_type == "history_context_pack" and root is not None:
        return execute_history_context_pack(request, root=root)
    return execute_stubbed_task(request, fallback_status=fallback_status)


def execute_history_context_pack(request: dict[str, Any], *, root: str | Path) -> dict[str, Any]:
    input_ref = str(request.get("input_ref", ""))
    try:
        input_document = _parse_input_ref(input_ref)
        channel_uuid = normalize_channel_uuid(str(request.get("channel_uuid") or input_document.get("channel_uuid") or ""))
        chunk_size = _positive_int(input_document.get("chunk_size"), default=50, label="chunk_size")
        limit = _optional_positive_int(input_document.get("messages"), label="messages")
        direction = str(input_document.get("direction") or RECENT_FIRST_BACKFILL)
        if direction not in {RECENT_FIRST_BACKFILL, OLDEST_FIRST_RECONSTRUCT}:
            raise ValueError(f"unsupported replay direction: {direction}")
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return {
            "status": "blocked",
            "result_summary": "history context pack input is invalid",
            "error_ref": f"history_context_pack:invalid_input:{error}",
            "retryable": False,
            "artifact_refs": [],
        }

    root_path = Path(root)
    history = ChannelJSONLHistory(root_path)
    resolver = PayloadResolver(PayloadStore(root_path))
    loaded_events = history.load(channel_uuid)
    request_message_uuid = str(input_document.get("request_message_uuid") or "")
    source_events, anchor_found = _events_before_anchor(loaded_events, request_message_uuid)
    event_types = tuple(input_document.get("event_types") or ("message",))
    filtered_events = [event for event in source_events if event.get("event_type") in set(event_types)]
    replay_events = _apply_replay_limit(filtered_events, limit=limit, direction=direction)
    chunks = _chunk_events(
        replay_events,
        resolver=resolver,
        channel_uuid=channel_uuid,
        chunk_size=chunk_size,
        direction=direction,
    )
    payloads = [row["payload"] for chunk in chunks for row in chunk["events"]]
    payload_errors = len([payload for payload in payloads if payload.get("event_type") == "payload_error"])
    resolved_payloads = len(payloads) - payload_errors
    created_at = utc_timestamp_ms()
    request_id = str(request.get("request_id", ""))
    pack = {
        "artifact_type": "history_context_pack",
        "artifact_version": 1,
        "request_id": request_id,
        "app_id": request.get("app_id"),
        "channel_uuid": channel_uuid,
        "created_at": created_at,
        "source_request": {
            "request_id": request_id,
            "requester_kind": request.get("requester_kind"),
            "requester_id": request.get("requester_id"),
            "task_type": request.get("task_type"),
            "input_ref": input_ref,
            "evidence_refs": request.get("evidence_refs", []),
        },
        "input": input_document,
        "anchor": {
            "request_message_uuid": request_message_uuid or None,
            "found": anchor_found,
            "policy": "events_before_request_message_when_anchor_is_available",
        },
        "replay_policy": {
            "chunk_size": chunk_size,
            "limit": limit,
            "direction": direction,
            "event_types": list(event_types),
        },
        "summary": {
            "chunk_count": len(chunks),
            "event_count": sum(chunk["event_count"] for chunk in chunks),
            "payload_errors": payload_errors,
            "resolved_payloads": resolved_payloads,
        },
        "chunks": chunks,
    }
    artifact_path, content_hash, size_bytes = _write_json_artifact(
        root_path / "codex_stream_artifacts" / channel_uuid / f"{_safe_file_stem(request_id)}.history_context_pack.json",
        pack,
    )
    artifact_uri = artifact_path.resolve().as_uri()
    return {
        "status": "completed",
        "result_summary": f"history context pack: {resolved_payloads} payloads across {len(chunks)} chunks",
        "result_ref": artifact_uri,
        "retryable": False,
        "artifact_refs": [artifact_uri],
        "artifacts": [
            {
                "artifact_uri": artifact_uri,
                "artifact_kind": "history_context_pack",
                "mime_type": "application/json",
                "content_hash": f"sha256:{content_hash}",
                "size_bytes": size_bytes,
                "metadata": {
                    "filesystem_path": str(artifact_path.resolve()),
                    "channel_uuid": channel_uuid,
                    "chunk_count": len(chunks),
                    "event_count": pack["summary"]["event_count"],
                    "payload_errors": payload_errors,
                    "resolved_payloads": resolved_payloads,
                    "anchor_found": anchor_found,
                    "request_message_uuid": request_message_uuid or None,
                },
            }
        ],
    }


def _parse_input_ref(input_ref: str) -> dict[str, Any]:
    value = json.loads(input_ref)
    if not isinstance(value, dict):
        raise ValueError("input_ref JSON must be an object")
    return value


def _positive_int(value: Any, *, default: int, label: str) -> int:
    if value is None:
        return default
    parsed = int(value)
    if parsed < 1:
        return default
    return parsed


def _optional_positive_int(value: Any, *, label: str) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{label} must be positive when provided")
    return parsed


def _events_before_anchor(events: list[dict[str, Any]], request_message_uuid: str) -> tuple[list[dict[str, Any]], bool]:
    if not request_message_uuid:
        return events, False
    for index, event in enumerate(events):
        if str(event.get("message_uuid")) == request_message_uuid:
            return events[:index], True
    return events, False


def _apply_replay_limit(
    events: list[dict[str, Any]],
    *,
    limit: int | None,
    direction: str,
) -> list[dict[str, Any]]:
    if limit is None:
        return list(events)
    if direction == RECENT_FIRST_BACKFILL:
        return events[-limit:]
    return events[:limit]


def _chunk_events(
    events: list[dict[str, Any]],
    *,
    resolver: PayloadResolver,
    channel_uuid: str,
    chunk_size: int,
    direction: str,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    if direction == RECENT_FIRST_BACKFILL:
        end = len(events)
        while end > 0:
            start = max(0, end - chunk_size)
            chunk_events = events[start:end]
            chunks.append(
                _context_pack_chunk(
                    chunk_events,
                    resolver=resolver,
                    channel_uuid=channel_uuid,
                    direction=direction,
                    index=len(chunks),
                    has_more=start > 0,
                )
            )
            end = start
        return chunks
    for start in range(0, len(events), chunk_size):
        chunk_events = events[start : start + chunk_size]
        chunks.append(
            _context_pack_chunk(
                chunk_events,
                resolver=resolver,
                channel_uuid=channel_uuid,
                direction=direction,
                index=len(chunks),
                has_more=start + chunk_size < len(events),
            )
        )
    return chunks


def _context_pack_chunk(
    events: list[dict[str, Any]],
    *,
    resolver: PayloadResolver,
    channel_uuid: str,
    direction: str,
    index: int,
    has_more: bool,
) -> dict[str, Any]:
    replayed = []
    for event in events:
        message_uuid = str(event.get("message_uuid", ""))
        replayed.append(
            {
                "event": event,
                "payload": resolver.resolve_render_model(message_uuid) if message_uuid else payload_error("", "missing_message_uuid"),
            }
        )
    return {
        "channel_uuid": channel_uuid,
        "direction": direction,
        "chunk_index": index,
        "event_count": len(replayed),
        "has_more": has_more,
        "events": replayed,
    }


def _write_json_artifact(target: Path, document: dict[str, Any]) -> tuple[Path, str, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    temp = target.with_suffix(target.suffix + ".tmp")
    try:
        with temp.open("wb") as file:
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()
    return target, hashlib.sha256(encoded).hexdigest(), len(encoded)


def _safe_file_stem(value: str) -> str:
    safe = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in value)
    return safe or "request"
