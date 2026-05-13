from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from .refinement_storage import ConversationRefinementStore, import_replay_rows
from .chat_platform import (
    AlienHandChatService,
    ChannelJSONLHistory,
    EnvelopeOutbox,
    JsonDict,
    PayloadResolver,
    PayloadStore,
    commit_message,
    default_ergo_root,
    irc_channel_name,
    payload_to_render_model,
    replay_channel,
)


PAYLOAD_RESOLVER_VERSION = "AH-PAYLOAD-RESOLVER/1"
RENDER_PATH_PREFIX = ("alienhand", "payloads")
REFINEMENT_PATH_PREFIX = ("alienhand", "refinement")


class PayloadResolverHTTPServer:
    def __init__(
        self,
        root: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        access_token: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.host = host
        self.port = 0 if port is None else port
        self.access_token = access_token
        self.store = PayloadStore(self.root)
        self.resolver = PayloadResolver(self.store)
        self.refinement_db_path = self.root / "refinement.sqlite3"
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
                if not owner._is_authorized(self):
                    self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                message_uuid = _message_uuid_from_render_path(self.path)
                if message_uuid is not None:
                    payload = owner.resolver.resolve_render_model(message_uuid)
                    self._send_json(payload_to_render_model(payload), HTTPStatus.OK)
                    return
                if self._handle_refinement_get():
                    return
                self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                if not owner._is_authorized(self):
                    self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                if self._handle_refinement_post():
                    return
                self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

            def do_DELETE(self) -> None:
                if not owner._is_authorized(self):
                    self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                if self._handle_refinement_delete():
                    return
                self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _handle_refinement_get(self) -> bool:
                parsed = urlparse(self.path)
                parts = _path_parts(parsed.path)
                query = _query_params(parsed.query)
                if parts == (*REFINEMENT_PATH_PREFIX, "blocks"):
                    try:
                        limit = _optional_positive_int(query.get("limit"), "limit")
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        blocks = store.list_blocks(channel_uuid=query.get("channel"), limit=limit)
                    self._send_json({"blocks": [block.to_dict() for block in blocks]}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "search"):
                    term = query.get("q", "")
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        hits = store.search(term)
                    self._send_json({"query": term, "hits": [hit.to_dict() for hit in hits]}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "cuts"):
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        cuts = store.list_cuts(status=query.get("status"), channel_uuid=query.get("channel"))
                    self._send_json({"cuts": [cut.to_dict() for cut in cuts]}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "chapters"):
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        chapters = store.list_chapters(channel_uuid=query.get("channel"))
                    self._send_json({"chapters": [chapter.to_dict() for chapter in chapters]}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "bookmarks"):
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            bookmarks = store.list_bookmarks(
                                channel_uuid=query.get("channel"),
                                target_type=query.get("target_type"),
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    self._send_json({"bookmarks": [bookmark.to_dict() for bookmark in bookmarks]}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "quotes"):
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            quotes = store.list_quotes(
                                channel_uuid=query.get("channel"),
                                source_type=query.get("source_type"),
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    self._send_json({"quotes": [quote.to_dict() for quote in quotes]}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "stickies"):
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            stickies = store.list_stickies(
                                channel_uuid=query.get("channel"),
                                target_type=query.get("target_type"),
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    self._send_json({"stickies": [sticky.to_dict() for sticky in stickies]}, HTTPStatus.OK)
                    return True
                return False

            def _handle_refinement_post(self) -> bool:
                parts = _path_parts(urlparse(self.path).path)
                if parts == (*REFINEMENT_PATH_PREFIX, "cuts"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    source_block_id = str(body.get("source_block_id") or "")
                    if not source_block_id:
                        self._send_json({"error": "source_block_id_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    position = body.get("position")
                    try:
                        resolved_position = (
                            None
                            if position is None
                            else _positive_int_value(position, "position", allow_zero=True)
                        )
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            cut = store.create_cut(source_block_id, position=resolved_position)
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    except KeyError:
                        self._send_json({"error": "source_block_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"cut": cut.to_dict()}, HTTPStatus.CREATED)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "chapters"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    title = str(body.get("title") or "")
                    summary = str(body.get("summary") or "")
                    cut_ids = body.get("cut_ids") or ()
                    if not title:
                        self._send_json({"error": "title_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not isinstance(cut_ids, list) or not cut_ids:
                        self._send_json({"error": "cut_ids_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            chapter = store.create_chapter(
                                title=title,
                                summary=summary,
                                cut_ids=tuple(str(cut_id) for cut_id in cut_ids),
                            )
                    except KeyError:
                        self._send_json({"error": "cut_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"chapter": chapter.to_dict()}, HTTPStatus.CREATED)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "bookmarks"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    target_type = str(body.get("target_type") or "")
                    target_id = str(body.get("target_id") or "")
                    if not target_type:
                        self._send_json({"error": "target_type_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not target_id:
                        self._send_json({"error": "target_id_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            bookmark = store.create_bookmark(
                                target_type=target_type,
                                target_id=target_id,
                                label=str(body.get("label") or ""),
                                note=str(body.get("note") or ""),
                                scope=str(body.get("scope") or "both"),
                                persistence=str(body.get("persistence") or "durable_channel"),
                                promotion_state=str(body.get("promotion_state") or "mirrored"),
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    except KeyError:
                        self._send_json({"error": "bookmark_target_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"bookmark": bookmark.to_dict()}, HTTPStatus.CREATED)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "quotes"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    source_type = str(body.get("source_type") or "")
                    source_id = str(body.get("source_id") or "")
                    excerpt = str(body.get("excerpt") or "")
                    if not source_type:
                        self._send_json({"error": "source_type_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not source_id:
                        self._send_json({"error": "source_id_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not excerpt:
                        self._send_json({"error": "excerpt_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    provenance = body.get("provenance")
                    if not isinstance(provenance, dict):
                        provenance = {}
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            quote = store.create_quote(
                                source_type=source_type,
                                source_id=source_id,
                                excerpt=excerpt,
                                provenance=provenance,
                                display_mode=str(body.get("display_mode") or "inline"),
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    except KeyError:
                        self._send_json({"error": "quote_source_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"quote": quote.to_dict()}, HTTPStatus.CREATED)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "stickies"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    target_type = str(body.get("target_type") or "")
                    target_id = str(body.get("target_id") or "")
                    if not target_type:
                        self._send_json({"error": "target_type_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not target_id:
                        self._send_json({"error": "target_id_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            sticky = store.create_sticky(
                                target_type=target_type,
                                target_id=target_id,
                                visibility=str(body.get("visibility") or "visible"),
                                retention=str(body.get("retention") or "session"),
                                clear_state=str(body.get("clear_state") or "active"),
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    except KeyError:
                        self._send_json({"error": "sticky_target_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"sticky": sticky.to_dict()}, HTTPStatus.CREATED)
                    return True
                return False

            def _handle_refinement_delete(self) -> bool:
                parts = _path_parts(urlparse(self.path).path)
                if len(parts) == 4 and tuple(parts[:3]) == (*REFINEMENT_PATH_PREFIX, "cuts"):
                    cut_id = parts[3]
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            cut = store.remove_cut(cut_id)
                    except KeyError:
                        self._send_json({"error": "cut_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"cut": cut.to_dict()}, HTTPStatus.OK)
                    return True
                return False

            def _read_json_body(self) -> JsonDict | None:
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self._send_json({"error": "invalid_content_length"}, HTTPStatus.BAD_REQUEST)
                    return None
                if length <= 0:
                    return {}
                try:
                    parsed = json.loads(self.rfile.read(length).decode("utf-8"))
                except json.JSONDecodeError:
                    self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
                    return None
                if not isinstance(parsed, dict):
                    self._send_json({"error": "json_object_required"}, HTTPStatus.BAD_REQUEST)
                    return None
                return parsed

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
                self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept")

        return PayloadResolverHandler

    def _is_authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        if not self.access_token:
            return True
        authorization = handler.headers.get("Authorization", "")
        token = ""
        scheme, separator, value = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            token = value.strip()
        return secrets.compare_digest(token, self.access_token)


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

    access_token = "payload-resolver-proof-token"

    with PayloadResolverHTTPServer(chat_root, access_token=access_token) as server:
        render_response = _http_json(server.render_url(published.envelope.message_uuid), access_token=access_token)
        missing_response = _http_json(server.render_url(missing_uuid), access_token=access_token)
        unauthorized_response = _http_json(server.render_url(published.envelope.message_uuid))
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
        "unauthorized_status": unauthorized_response["status"],
        "cors_ok": cors_ok,
        "payload_resolver_fetch_ok": render_row.get("status") == "resolved"
        and render_row.get("orientation") == "right"
        and frame_kinds == ["code", "link"]
        and missing_row.get("status") == "payload_error"
        and unauthorized_response["status"] == 401
        and cors_ok,
        "root": str(chat_root.resolve()),
    }


def run_refinement_http_api_proof(root: str | Path, *, app_id: int = 1) -> JsonDict:
    chat_root = Path(root)
    store = PayloadStore(chat_root)
    history = ChannelJSONLHistory(chat_root)
    outbox = EnvelopeOutbox(chat_root / "irc_outbox.jsonl")
    channel_uuid = uuid4().hex

    published = commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="user",
        sender_type="user",
        payload_kind="text",
        content={"text": "searchable workbench source block"},
        metadata={"proof": "refinement_http_api"},
        store=store,
        history=history,
        publisher=outbox,
    )
    replayed = replay_channel(history, PayloadResolver(store), channel_uuid)
    with ConversationRefinementStore(chat_root / "refinement.sqlite3") as refinement_store:
        imported_blocks = import_replay_rows(refinement_store, replayed)

    access_token = "refinement-http-proof-token"
    with PayloadResolverHTTPServer(chat_root, access_token=access_token) as server:
        blocks_response = _http_json(
            f"{server.base_url}/alienhand/refinement/blocks?channel={channel_uuid}",
            access_token=access_token,
        )
        search_response = _http_json(
            f"{server.base_url}/alienhand/refinement/search?q=workbench",
            access_token=access_token,
        )
        unauthorized_response = _http_json(f"{server.base_url}/alienhand/refinement/blocks")
        cut_response = _http_post_json(
            f"{server.base_url}/alienhand/refinement/cuts",
            {"source_block_id": imported_blocks[0].block_id},
            access_token=access_token,
        )
        cut_id = cut_response["json"].get("cut", {}).get("cut_id")
        chapter_response = _http_post_json(
            f"{server.base_url}/alienhand/refinement/chapters",
            {"title": "Workbench Sources", "summary": "User-selected proof cut.", "cut_ids": [cut_id]},
            access_token=access_token,
        )
        bookmark_response = _http_post_json(
            f"{server.base_url}/alienhand/refinement/bookmarks",
            {
                "target_type": "block",
                "target_id": imported_blocks[0].block_id,
                "label": "Remember source",
                "note": "Bookmark note follows the source block.",
            },
            access_token=access_token,
        )
        quote_response = _http_post_json(
            f"{server.base_url}/alienhand/refinement/quotes",
            {
                "source_type": "block",
                "source_id": imported_blocks[0].block_id,
                "excerpt": "searchable workbench source",
                "provenance": {"block_id": imported_blocks[0].block_id},
            },
            access_token=access_token,
        )
        sticky_response = _http_post_json(
            f"{server.base_url}/alienhand/refinement/stickies",
            {
                "target_type": "bookmark",
                "target_id": bookmark_response["json"].get("bookmark", {}).get("bookmark_id"),
            },
            access_token=access_token,
        )
        remove_response = _http_delete_json(
            f"{server.base_url}/alienhand/refinement/cuts/{cut_id}",
            access_token=access_token,
        )
        cuts_response = _http_json(
            f"{server.base_url}/alienhand/refinement/cuts?channel={channel_uuid}",
            access_token=access_token,
        )
        chapters_response = _http_json(
            f"{server.base_url}/alienhand/refinement/chapters?channel={channel_uuid}",
            access_token=access_token,
        )
        bookmarks_response = _http_json(
            f"{server.base_url}/alienhand/refinement/bookmarks?channel={channel_uuid}",
            access_token=access_token,
        )
        quotes_response = _http_json(
            f"{server.base_url}/alienhand/refinement/quotes?channel={channel_uuid}",
            access_token=access_token,
        )
        stickies_response = _http_json(
            f"{server.base_url}/alienhand/refinement/stickies?channel={channel_uuid}",
            access_token=access_token,
        )
        base_url = server.base_url

    blocks = blocks_response["json"].get("blocks", [])
    hits = search_response["json"].get("hits", [])
    cuts = cuts_response["json"].get("cuts", [])
    chapters = chapters_response["json"].get("chapters", [])
    bookmarks = bookmarks_response["json"].get("bookmarks", [])
    quotes = quotes_response["json"].get("quotes", [])
    stickies = stickies_response["json"].get("stickies", [])
    refinement_http_ok = (
        blocks_response["status"] == 200
        and len(blocks) == 1
        and blocks[0].get("block_id") == imported_blocks[0].block_id
        and search_response["status"] == 200
        and [hit.get("block_id") for hit in hits] == [imported_blocks[0].block_id]
        and unauthorized_response["status"] == 401
        and cut_response["status"] == 201
        and chapter_response["status"] == 201
        and bookmark_response["status"] == 201
        and bookmark_response["json"].get("bookmark", {}).get("note") == "Bookmark note follows the source block."
        and quote_response["status"] == 201
        and quote_response["json"].get("quote", {}).get("excerpt") == "searchable workbench source"
        and sticky_response["status"] == 201
        and sticky_response["json"].get("sticky", {}).get("target_id")
        == bookmark_response["json"].get("bookmark", {}).get("bookmark_id")
        and remove_response["status"] == 200
        and remove_response["json"].get("cut", {}).get("status") == "removed"
        and len(cuts) == 1
        and len(chapters) == 1
        and len(bookmarks) == 1
        and bookmarks[0].get("target_id") == imported_blocks[0].block_id
        and len(quotes) == 1
        and quotes[0].get("source_id") == imported_blocks[0].block_id
        and len(stickies) == 1
    )
    return {
        "resolver_version": PAYLOAD_RESOLVER_VERSION,
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "message_uuid": published.envelope.message_uuid,
        "refinement_base_url": base_url,
        "imported_blocks": len(imported_blocks),
        "listed_blocks": len(blocks),
        "search_hits": len(hits),
        "created_cut_id": cut_id,
        "created_chapter_id": chapter_response["json"].get("chapter", {}).get("chapter_id"),
        "created_bookmark_id": bookmark_response["json"].get("bookmark", {}).get("bookmark_id"),
        "bookmark_note": bookmark_response["json"].get("bookmark", {}).get("note"),
        "created_quote_id": quote_response["json"].get("quote", {}).get("quote_id"),
        "quote_excerpt": quote_response["json"].get("quote", {}).get("excerpt"),
        "created_sticky_id": sticky_response["json"].get("sticky", {}).get("sticky_id"),
        "sticky_target_type": sticky_response["json"].get("sticky", {}).get("target_type"),
        "removed_cut_status": remove_response["json"].get("cut", {}).get("status"),
        "listed_cuts": len(cuts),
        "listed_chapters": len(chapters),
        "listed_bookmarks": len(bookmarks),
        "listed_quotes": len(quotes),
        "listed_stickies": len(stickies),
        "unauthorized_status": unauthorized_response["status"],
        "refinement_http_ok": refinement_http_ok,
        "root": str(chat_root.resolve()),
    }


def run_app_payload_resolver_lifecycle_proof(
    root: str | Path,
    *,
    ergo_root: str | Path | None = None,
    app_id: int = 1,
    nick: str = "alienhandagent",
    text: str = "hello app-owned payload resolver",
    port: int | None = None,
    timeout: float = 30.0,
) -> JsonDict:
    chat_root = Path(root)
    channel_uuid = uuid4().hex

    with AlienHandChatService(
        chat_root,
        ergo_root=ergo_root,
        app_id=app_id,
        nick=nick,
        port=port,
        startup_timeout=timeout,
        build_timeout=timeout,
    ) as service:
        published = service.publish_text(
            text,
            channel_uuid=channel_uuid,
            metadata={"proof": "app_payload_resolver_lifecycle"},
        )
        process_started = service.process is not None and service.process.poll() is None
        resolver_started = service.payload_http_server is not None
        resolver_base_url = service.payload_resolver_base_url
        resolver_port = service.payload_resolver_port
        thelounge_environment = service.thelounge_environment()
        ergo_port = service.port
        render_url = service.payload_http_server.render_url(published.envelope.message_uuid)
        render_response = _http_json(render_url, access_token=service.payload_resolver_token)

    replayed = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    resolved_payloads = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]
    render_row = render_response["json"]
    fetch_ok = (
        render_response["status"] == 200
        and render_row.get("status") == "resolved"
        and render_row.get("message_uuid") == published.envelope.message_uuid
        and render_row.get("content", {}).get("text") == text
    )
    return {
        "resolver_version": PAYLOAD_RESOLVER_VERSION,
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "irc_channel": irc_channel_name(channel_uuid),
        "message_uuid": published.envelope.message_uuid,
        "envelope": published.envelope.to_line(),
        "ergo_root": str((Path(ergo_root) if ergo_root else default_ergo_root()).resolve()),
        "ergo_port": ergo_port,
        "resolver_base_url": resolver_base_url,
        "resolver_port": resolver_port,
        "thelounge_payload_resolver_env": thelounge_environment["ALIENHAND_PAYLOAD_RESOLVER"],
        "thelounge_payload_resolver_token_present": bool(thelounge_environment["ALIENHAND_PAYLOAD_RESOLVER_TOKEN"]),
        "render_url": render_url,
        "app_lifecycle_started": process_started,
        "payload_resolver_started": resolver_started,
        "payload_resolver_fetch_ok": fetch_ok,
        "payload_resolver_stopped": service.payload_http_server is None,
        "history_events": len(replayed),
        "resolved_payloads": len(resolved_payloads),
        "cold_replay_ok": len(resolved_payloads) == 1,
        "app_payload_resolver_lifecycle_ok": process_started
        and resolver_started
        and fetch_ok
        and service.payload_http_server is None
        and len(resolved_payloads) == 1,
        "root": str(chat_root.resolve()),
    }


def run_app_thelounge_runtime_group_proof(
    root: str | Path,
    *,
    ergo_root: str | Path | None = None,
    thelounge_root: str | Path | None = None,
    app_id: int = 1,
    nick: str = "alienhandagent",
    text: str = "hello app-owned thelounge",
    port: int | None = None,
    thelounge_port: int | None = None,
    timeout: float = 30.0,
) -> JsonDict:
    chat_root = Path(root)
    channel_uuid = uuid4().hex

    with AlienHandChatService(
        chat_root,
        ergo_root=ergo_root,
        thelounge_root=thelounge_root,
        app_id=app_id,
        nick=nick,
        port=port,
        thelounge_port=thelounge_port,
        start_thelounge=True,
        startup_timeout=timeout,
        build_timeout=timeout,
    ) as service:
        published = service.publish_text(
            text,
            channel_uuid=channel_uuid,
            metadata={"proof": "app_thelounge_runtime_group"},
        )
        process_started = service.process is not None and service.process.poll() is None
        resolver_started = service.payload_http_server is not None
        thelounge_started = service.thelounge_process is not None and service.thelounge_process.poll() is None
        resolver_base_url = service.payload_resolver_base_url
        thelounge_base_url = service.thelounge_base_url
        thelounge_environment = service.thelounge_environment()
        ergo_port = service.port
        resolved_thelounge_port = service.thelounge_port
        if service.payload_http_server is None or thelounge_base_url is None:
            raise RuntimeError("runtime group proof requires payload resolver and The Lounge to be running")
        render_url = service.payload_http_server.render_url(published.envelope.message_uuid)
        render_response = _http_json(render_url, access_token=service.payload_resolver_token)
        index_response = _http_text(f"{thelounge_base_url}/")

    replayed = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    resolved_payloads = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]
    index_html = index_response["text"]
    render_row = render_response["json"]
    resolver_data_attribute = f'data-alienhand-payload-resolver="{resolver_base_url}"'
    resolver_token_attribute = f'data-alienhand-payload-resolver-token="{service.payload_resolver_token}"'
    thelounge_html_has_resolver = resolver_data_attribute in index_html
    thelounge_html_has_resolver_token = resolver_token_attribute in index_html
    fetch_ok = (
        render_response["status"] == 200
        and render_row.get("status") == "resolved"
        and render_row.get("message_uuid") == published.envelope.message_uuid
        and render_row.get("content", {}).get("text") == text
    )
    return {
        "resolver_version": PAYLOAD_RESOLVER_VERSION,
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "irc_channel": irc_channel_name(channel_uuid),
        "message_uuid": published.envelope.message_uuid,
        "envelope": published.envelope.to_line(),
        "ergo_root": str((Path(ergo_root) if ergo_root else default_ergo_root()).resolve()),
        "ergo_port": ergo_port,
        "resolver_base_url": resolver_base_url,
        "thelounge_base_url": thelounge_base_url,
        "thelounge_port": resolved_thelounge_port,
        "thelounge_payload_resolver_env": thelounge_environment["ALIENHAND_PAYLOAD_RESOLVER"],
        "thelounge_payload_resolver_token_present": bool(thelounge_environment["ALIENHAND_PAYLOAD_RESOLVER_TOKEN"]),
        "render_url": render_url,
        "app_lifecycle_started": process_started,
        "payload_resolver_started": resolver_started,
        "thelounge_started": thelounge_started,
        "payload_resolver_fetch_ok": fetch_ok,
        "thelounge_html_has_resolver": thelounge_html_has_resolver,
        "thelounge_html_has_resolver_token": thelounge_html_has_resolver_token,
        "payload_resolver_stopped": service.payload_http_server is None,
        "thelounge_stopped": service.thelounge_process is None,
        "history_events": len(replayed),
        "resolved_payloads": len(resolved_payloads),
        "cold_replay_ok": len(resolved_payloads) == 1,
        "runtime_group_ok": process_started
        and resolver_started
        and thelounge_started
        and fetch_ok
        and thelounge_html_has_resolver
        and thelounge_html_has_resolver_token
        and service.payload_http_server is None
        and service.thelounge_process is None
        and len(resolved_payloads) == 1,
        "root": str(chat_root.resolve()),
    }


def run_app_thelounge_refinement_workbench_proof(
    root: str | Path,
    *,
    ergo_root: str | Path | None = None,
    thelounge_root: str | Path | None = None,
    app_id: int = 1,
    nick: str = "alienhandagent",
    text: str = "hello app-owned refinement workbench",
    port: int | None = None,
    thelounge_port: int | None = None,
    timeout: float = 30.0,
) -> JsonDict:
    chat_root = Path(root)
    channel_uuid = uuid4().hex

    with AlienHandChatService(
        chat_root,
        ergo_root=ergo_root,
        thelounge_root=thelounge_root,
        app_id=app_id,
        nick=nick,
        port=port,
        thelounge_port=thelounge_port,
        start_thelounge=True,
        startup_timeout=timeout,
        build_timeout=timeout,
    ) as service:
        published = service.publish_text(
            text,
            channel_uuid=channel_uuid,
            metadata={"proof": "app_thelounge_refinement_workbench"},
        )
        process_started = service.process is not None and service.process.poll() is None
        resolver_started = service.payload_http_server is not None
        thelounge_started = service.thelounge_process is not None and service.thelounge_process.poll() is None
        resolver_base_url = service.payload_resolver_base_url
        thelounge_base_url = service.thelounge_base_url
        if resolver_base_url is None or service.payload_http_server is None or thelounge_base_url is None:
            raise RuntimeError("workbench proof requires payload resolver and The Lounge to be running")

        access_token = service.payload_resolver_token
        blocks_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/blocks?channel={channel_uuid}",
            access_token=access_token,
        )
        search_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/search?q=workbench",
            access_token=access_token,
        )
        blocks = blocks_response["json"].get("blocks", [])
        block_id = blocks[0].get("block_id") if blocks else ""
        cut_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/cuts",
            {"source_block_id": block_id},
            access_token=access_token,
        )
        cut_id = cut_response["json"].get("cut", {}).get("cut_id")
        chapter_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/chapters",
            {
                "cut_ids": [cut_id],
                "summary": "Runtime workbench proof chapter.",
                "title": "Workbench Runtime",
            },
            access_token=access_token,
        )
        remove_response = _http_delete_json(
            f"{resolver_base_url}/alienhand/refinement/cuts/{cut_id}",
            access_token=access_token,
        )
        cuts_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/cuts?channel={channel_uuid}",
            access_token=access_token,
        )
        active_cuts_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/cuts?channel={channel_uuid}&status=active",
            access_token=access_token,
        )
        chapters_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/chapters?channel={channel_uuid}",
            access_token=access_token,
        )
        render_response = _http_json(
            service.payload_http_server.render_url(published.envelope.message_uuid),
            access_token=access_token,
        )
        index_response = _http_text(f"{thelounge_base_url}/")
        bundle_response = _http_text(f"{thelounge_base_url}/js/bundle.js")
        style_response = _http_text(f"{thelounge_base_url}/css/style.css")
        ergo_port = service.port
        resolved_thelounge_port = service.thelounge_port

    replayed = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    resolved_payloads = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]
    render_row = render_response["json"]
    search_hits = search_response["json"].get("hits", [])
    cuts = cuts_response["json"].get("cuts", [])
    active_cuts = active_cuts_response["json"].get("cuts", [])
    chapters = chapters_response["json"].get("chapters", [])
    index_html = index_response["text"]
    bundle_js = bundle_response["text"]
    style_css = style_response["text"]
    resolver_data_attribute = f'data-alienhand-payload-resolver="{resolver_base_url}"'
    workbench_bundle_ok = (
        "AlienHand refinement" in bundle_js
        and "Inject into cuts" in bundle_js
        and "Bookmark note" in bundle_js
        and "Bookmark:" in bundle_js
        and "alienhand-workbench" in bundle_js
    )
    workbench_style_ok = "alienhand-workbench" in style_css and "alienhand-workbench__bookmarks" in style_css
    refinement_api_ok = (
        blocks_response["status"] == 200
        and len(blocks) == 1
        and blocks[0].get("message_uuid") == published.envelope.message_uuid
        and search_response["status"] == 200
        and [hit.get("block_id") for hit in search_hits] == [block_id]
        and cut_response["status"] == 201
        and chapter_response["status"] == 201
        and remove_response["status"] == 200
        and remove_response["json"].get("cut", {}).get("status") == "removed"
        and len(cuts) == 1
        and len(active_cuts) == 0
        and len(chapters) == 1
    )
    render_fetch_ok = (
        render_response["status"] == 200
        and render_row.get("status") == "resolved"
        and render_row.get("message_uuid") == published.envelope.message_uuid
    )
    workbench_runtime_ok = (
        process_started
        and resolver_started
        and thelounge_started
        and resolver_data_attribute in index_html
        and workbench_bundle_ok
        and workbench_style_ok
        and refinement_api_ok
        and render_fetch_ok
        and service.payload_http_server is None
        and service.thelounge_process is None
        and len(resolved_payloads) == 1
    )
    return {
        "resolver_version": PAYLOAD_RESOLVER_VERSION,
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "irc_channel": irc_channel_name(channel_uuid),
        "message_uuid": published.envelope.message_uuid,
        "ergo_port": ergo_port,
        "resolver_base_url": resolver_base_url,
        "thelounge_base_url": thelounge_base_url,
        "thelounge_port": resolved_thelounge_port,
        "app_lifecycle_started": process_started,
        "payload_resolver_started": resolver_started,
        "thelounge_started": thelounge_started,
        "render_fetch_ok": render_fetch_ok,
        "listed_blocks": len(blocks),
        "search_hits": len(search_hits),
        "created_cut_id": cut_id,
        "removed_cut_status": remove_response["json"].get("cut", {}).get("status"),
        "listed_cuts": len(cuts),
        "active_cuts_after_remove": len(active_cuts),
        "listed_chapters": len(chapters),
        "workbench_bundle_ok": workbench_bundle_ok,
        "workbench_style_ok": workbench_style_ok,
        "refinement_api_ok": refinement_api_ok,
        "payload_resolver_stopped": service.payload_http_server is None,
        "thelounge_stopped": service.thelounge_process is None,
        "history_events": len(replayed),
        "resolved_payloads": len(resolved_payloads),
        "cold_replay_ok": len(resolved_payloads) == 1,
        "workbench_runtime_ok": workbench_runtime_ok,
        "root": str(chat_root.resolve()),
    }


def _message_uuid_from_render_path(path: str) -> str | None:
    parsed = urlparse(path)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) == 4 and tuple(parts[:2]) == RENDER_PATH_PREFIX and parts[3] == "render":
        return parts[2]
    return None


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(unquote(part) for part in path.split("/") if part)


def _query_params(query: str) -> dict[str, str]:
    parsed = parse_qs(query, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


def _optional_positive_int(value: str | None, name: str) -> int | None:
    if value is None or value == "":
        return None
    return _positive_int_value(value, name)


def _positive_int_value(value: Any, name: str, *, allow_zero: bool = False) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    minimum = 0 if allow_zero else 1
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed


def _http_json(url: str, *, access_token: str | None = None) -> JsonDict:
    headers = {"Accept": "application/json", "Origin": "http://127.0.0.1"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=5.0) as response:
            body = response.read().decode("utf-8")
            return {"json": json.loads(body), "headers": dict(response.headers), "status": response.status}
    except HTTPError as error:
        body = error.read().decode("utf-8")
        return {"json": json.loads(body), "headers": dict(error.headers), "status": error.code}


def _http_post_json(url: str, body: JsonDict, *, access_token: str | None = None) -> JsonDict:
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    encoded = json.dumps(body, sort_keys=True).encode("utf-8")
    request = Request(url, data=encoded, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=5.0) as response:
            response_body = response.read().decode("utf-8")
            return {"json": json.loads(response_body), "headers": dict(response.headers), "status": response.status}
    except HTTPError as error:
        response_body = error.read().decode("utf-8")
        return {"json": json.loads(response_body), "headers": dict(error.headers), "status": error.code}


def _http_delete_json(url: str, *, access_token: str | None = None) -> JsonDict:
    headers = {"Accept": "application/json", "Origin": "http://127.0.0.1"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(url, headers=headers, method="DELETE")
    try:
        with urlopen(request, timeout=5.0) as response:
            response_body = response.read().decode("utf-8")
            return {"json": json.loads(response_body), "headers": dict(response.headers), "status": response.status}
    except HTTPError as error:
        response_body = error.read().decode("utf-8")
        return {"json": json.loads(response_body), "headers": dict(error.headers), "status": error.code}


def _http_options(url: str) -> JsonDict:
    request = Request(url, headers={"Origin": "http://127.0.0.1"}, method="OPTIONS")
    with urlopen(request, timeout=5.0) as response:
        response.read()
        return {"headers": dict(response.headers), "status": response.status}


def _http_text(url: str) -> JsonDict:
    request = Request(url)
    with urlopen(request, timeout=5.0) as response:
        return {"text": response.read().decode("utf-8"), "headers": dict(response.headers), "status": response.status}
