from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .chat_platform import JsonDict, PayloadObject, payload_to_render_model, utc_timestamp_ms


ADAPTER_VERSION = "AH-THELOUNGE/1"


def sample_thelounge_render_rows(app_id: int = 1) -> list[JsonDict]:
    channel_uuid = uuid4().hex
    rows = [
        payload_to_render_model(
            PayloadObject(
                message_uuid=str(uuid4()),
                app_id=app_id,
                channel_uuid=channel_uuid,
                sender="user",
                sender_type="user",
                event_type="message",
                payload_kind="text",
                created_at=utc_timestamp_ms(),
                content={"text": "Can you show me the path?"},
            )
        ),
        payload_to_render_model(
            PayloadObject(
                message_uuid=str(uuid4()),
                app_id=app_id,
                channel_uuid=channel_uuid,
                sender="agent",
                sender_type="ai_agent",
                event_type="message",
                payload_kind="mixed",
                created_at=utc_timestamp_ms(),
                content={"text": "Here is the minimal renderer contract."},
                frames=(
                    {"kind": "code", "language": "python", "code": "print('AlienHand render row')"},
                    {"kind": "image", "source": "payloads/example.png", "alt": "example payload image"},
                    {"kind": "link", "source": "https://thelounge.chat/", "label": "thelounge"},
                ),
            )
        ),
        payload_to_render_model(
            {
                "message_uuid": str(uuid4()),
                "sender": "payload_resolver",
                "sender_type": "service",
                "event_type": "payload_error",
                "payload_kind": "system",
                "created_at": utc_timestamp_ms(),
                "content": {"reason": "payload_not_found"},
                "frames": [],
                "metadata": {},
            }
        ),
    ]
    return rows


def build_thelounge_preview_html(rows: list[JsonDict], *, title: str = "AlienHand thelounge Adapter Preview") -> str:
    rendered_rows = "\n".join(_render_preview_row(row) for row in rows)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<link rel="stylesheet" href="../../../thelounge-alienhand/client/css/alienhand-chat.css">
</head>
<body>
<main class="alienhand-preview" data-adapter-version="{ADAPTER_VERSION}">
{rendered_rows}
</main>
</body>
</html>
"""


def write_thelounge_adapter_preview(root: str | Path, rows: list[JsonDict]) -> JsonDict:
    output_root = Path(root)
    output_root.mkdir(parents=True, exist_ok=True)
    fixture_path = output_root / "render_rows.json"
    preview_path = output_root / "preview.html"
    fixture_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    preview_path.write_text(build_thelounge_preview_html(rows), encoding="utf-8")
    return {
        "adapter_version": ADAPTER_VERSION,
        "fixture_path": str(fixture_path.resolve()),
        "preview_path": str(preview_path.resolve()),
        "summary": summarize_thelounge_render_rows(rows),
    }


def summarize_thelounge_render_rows(rows: list[JsonDict]) -> JsonDict:
    frame_kinds = [frame.get("kind", "") for row in rows for frame in row.get("frames", [])]
    return {
        "rows": len(rows),
        "orientations": [row.get("orientation") for row in rows],
        "frame_kinds": frame_kinds,
        "payload_error_rows": len([row for row in rows if row.get("status") == "payload_error"]),
        "adapter_ok": [row.get("orientation") for row in rows] == ["left", "right", "system"]
        and {"code", "image", "link"}.issubset(set(frame_kinds))
        and any(row.get("status") == "payload_error" for row in rows),
    }


def run_thelounge_adapter_proof(root: str | Path, *, app_id: int = 1) -> JsonDict:
    rows = sample_thelounge_render_rows(app_id=app_id)
    result = write_thelounge_adapter_preview(root, rows)
    result["root"] = str(Path(root).resolve())
    return result


def _render_preview_row(row: JsonDict) -> str:
    frames = "\n".join(_render_preview_frame(frame) for frame in row.get("frames", []))
    content = row.get("content", {})
    text = escape(str(content.get("text") or content.get("reason") or ""))
    return f"""<article class="alienhand-message alienhand-message--{escape(str(row.get("orientation", "system")))}" data-message-uuid="{escape(str(row.get("message_uuid", "")))}">
<header class="alienhand-message__meta">
<span class="alienhand-message__sender">{escape(str(row.get("sender", row.get("sender_type", ""))))}</span>
<time class="alienhand-message__time">{escape(str(row.get("created_at", "")))}</time>
</header>
<div class="alienhand-message__bubble">
<p class="alienhand-message__text">{text}</p>
{frames}
</div>
</article>"""


def _render_preview_frame(frame: dict[str, Any]) -> str:
    kind = str(frame.get("kind", "unknown"))
    if kind == "code":
        return f"""<pre class="alienhand-frame alienhand-frame--code"><code>{escape(str(frame.get("text", "")))}</code></pre>"""
    if kind == "image":
        source = escape(str(frame.get("source", "")))
        alt = escape(str(frame.get("alt", "")))
        return f"""<figure class="alienhand-frame alienhand-frame--image"><img src="{source}" alt="{alt}"><figcaption>{alt}</figcaption></figure>"""
    if kind in {"file", "link"}:
        source = escape(str(frame.get("source", "")))
        label = escape(str(frame.get("label") or frame.get("source", "")))
        return f"""<a class="alienhand-frame alienhand-frame--link" href="{source}">{label}</a>"""
    return f"""<pre class="alienhand-frame alienhand-frame--unknown">{escape(json.dumps(frame, indent=2, sort_keys=True))}</pre>"""
