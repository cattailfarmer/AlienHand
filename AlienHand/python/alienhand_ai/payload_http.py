from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .chat_platform import (
    ChannelJSONLHistory,
    EnvelopeOutbox,
    JsonDict,
    PayloadResolver,
    PayloadStore,
    commit_message,
    payload_to_render_model,
)


PAYLOAD_RESOLVER_VERSION = "AH-PAYLOAD-RESOLVER/1"
RENDER_PATH_PREFIX = ("alienhand", "payloads")


class PayloadResolverHTTPServer:
    def __init__(
        self,
        root: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
    ) -> None:
        self.root = Path(root)
        self.host = host
        self.port = 0 if port is None else port
        self.store = PayloadStore(self.root)
        self.resolver = PayloadResolver(self.store)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def render_url(self, message_uuid: str) -> str:
        return f"{self.base_url}/alienhand/payloads/{message_uuid}/render"

    def start(self) -> "PayloadResolverHTTPServer":
        if self._server is not None:
            return self
        self.root.mkdir(parents=True, exist_ok=True)
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler_class())
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> "PayloadResolverHTTPServer":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class PayloadResolverHandler(BaseHTTPRequestHandler):
            server_version = PAYLOAD_RESOLVER_VERSION

            def do_OPTIONS(self) -> None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self._send_cors_headers()
                self.end_headers()

            def do_GET(self) -> None:
                message_uuid = _message_uuid_from_render_path(self.path)
                if message_uuid is None:
                    self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return
                payload = owner.resolver.resolve_render_model(message_uuid)
                self._send_json(payload_to_render_model(payload), HTTPStatus.OK)

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send_json(self, body: JsonDict, status: HTTPStatus) -> None:
                encoded = json.dumps(body, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _send_cors_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")

        return PayloadResolverHandler


def run_payload_resolver_http_proof(root: str | Path, *, app_id: int = 1) -> JsonDict:
    chat_root = Path(root)
    store = PayloadStore(chat_root)
    history = ChannelJSONLHistory(chat_root)
    outbox = EnvelopeOutbox(chat_root / "irc_outbox.jsonl")
    channel_uuid = uuid4().hex

    published = commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="agent",
        sender_type="ai_agent",
        payload_kind="mixed",
        content={"text": "live payload resolver proof"},
        frames=(
            {"kind": "code", "language": "python", "code": "print('resolver')"},
            {"kind": "link", "source": "https://example.invalid/payload", "label": "payload link"},
        ),
        metadata={"proof": "payload_resolver_http"},
        store=store,
        history=history,
        publisher=outbox,
    )
    missing_uuid = str(uuid4())

    with PayloadResolverHTTPServer(chat_root) as server:
        render_response = _http_json(server.render_url(published.envelope.message_uuid))
        missing_response = _http_json(server.render_url(missing_uuid))
        options_response = _http_options(server.render_url(published.envelope.message_uuid))
        base_url = server.base_url
        render_url = server.render_url(published.envelope.message_uuid)

    render_row = render_response["json"]
    missing_row = missing_response["json"]
    frame_kinds = [frame["kind"] for frame in render_row.get("frames", [])]
    cors_ok = (
        render_response["headers"].get("Access-Control-Allow-Origin") == "*"
        and options_response["headers"].get("Access-Control-Allow-Origin") == "*"
    )

    return {
        "resolver_version": PAYLOAD_RESOLVER_VERSION,
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "message_uuid": published.envelope.message_uuid,
        "resolver_base_url": base_url,
        "render_url": render_url,
        "resolved_status": render_row.get("status"),
        "resolved_orientation": render_row.get("orientation"),
        "resolved_text": render_row.get("content", {}).get("text"),
        "frame_kinds": frame_kinds,
        "missing_status": missing_row.get("status"),
        "missing_reason": missing_row.get("content", {}).get("reason"),
        "cors_ok": cors_ok,
        "payload_resolver_fetch_ok": render_row.get("status") == "resolved"
        and render_row.get("orientation") == "right"
        and frame_kinds == ["code", "link"]
        and missing_row.get("status") == "payload_error"
        and cors_ok,
        "root": str(chat_root.resolve()),
    }


def _message_uuid_from_render_path(path: str) -> str | None:
    parsed = urlparse(path)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) == 4 and tuple(parts[:2]) == RENDER_PATH_PREFIX and parts[3] == "render":
        return parts[2]
    return None


def _http_json(url: str) -> JsonDict:
    request = Request(url, headers={"Accept": "application/json", "Origin": "http://127.0.0.1"})
    with urlopen(request, timeout=5.0) as response:
        body = response.read().decode("utf-8")
        return {"json": json.loads(body), "headers": dict(response.headers), "status": response.status}


def _http_options(url: str) -> JsonDict:
    request = Request(url, headers={"Origin": "http://127.0.0.1"}, method="OPTIONS")
    with urlopen(request, timeout=5.0) as response:
        response.read()
        return {"headers": dict(response.headers), "status": response.status}
