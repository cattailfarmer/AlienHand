from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from .refinement_storage import ConversationBlock, ConversationRefinementStore, import_replay_rows
from .chat_platform import (
    AlienHandChatService,
    ChannelJSONLHistory,
    DEFAULT_HISTORY_COMMAND_CHUNK_SIZE,
    DEFAULT_HISTORY_COMMAND_MESSAGES,
    EnvelopeOutbox,
    JsonDict,
    MAX_HISTORY_COMMAND_CHUNK_SIZE,
    MAX_HISTORY_COMMAND_MESSAGES,
    PayloadResolver,
    PayloadStore,
    RECENT_FIRST_BACKFILL,
    commit_message,
    default_ergo_root,
    irc_channel_name,
    normalize_channel_uuid,
    payload_to_render_model,
    replay_channel,
    replay_channel_chunks,
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
        allowed_origins: Iterable[str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.host = host
        self.port = 0 if port is None else port
        self.access_token = access_token
        self.allowed_origins = tuple(
            dict.fromkeys(origin.rstrip("/") for origin in (allowed_origins or ()) if origin)
        )
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

    def cors_origin_for(self, request_origin: str | None) -> str | None:
        if not self.allowed_origins:
            return "*"
        if request_origin in self.allowed_origins:
            return request_origin
        return None

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

            def _record_refinement_directive(
                self,
                store: ConversationRefinementStore,
                *,
                channel_uuid: str,
                directive_kind: str,
                body: JsonDict,
                target_type: str = "",
                target_id: str = "",
                result_ref: JsonDict | None = None,
            ) -> None:
                store.record_directive(
                    channel_uuid=channel_uuid,
                    directive_kind=directive_kind,
                    source=str(body.get("source") or "user"),
                    visibility=str(body.get("visibility") or "debug_only"),
                    target_type=target_type,
                    target_id=target_id,
                    payload=body,
                    result_ref=result_ref or {},
                )

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
                    channel_uuid = query.get("channel")
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        hits = store.search(term, channel_uuid=channel_uuid)
                    self._send_json(
                        {
                            "query": term,
                            "channel": channel_uuid,
                            "hits": [hit.to_dict() for hit in hits],
                        },
                        HTTPStatus.OK,
                    )
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
                                clear_state=query.get("clear_state"),
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    self._send_json({"stickies": [sticky.to_dict() for sticky in stickies]}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "edits"):
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        edits = store.list_edits(edit_type=query.get("edit_type"))
                    self._send_json({"edits": [edit.to_dict() for edit in edits]}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "edit-diffs"):
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        diffs = store.list_edit_diffs(edit_id=query.get("edit_id"))
                    self._send_json({"diffs": [diff.to_dict() for diff in diffs]}, HTTPStatus.OK)
                    return True
                if len(parts) == 4 and tuple(parts[:3]) == (*REFINEMENT_PATH_PREFIX, "edit-diffs"):
                    diff_id = parts[3]
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            diff = store.get_edit_diff(diff_id)
                    except KeyError:
                        self._send_json({"error": "edit_diff_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"diff": diff.to_dict()}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "toc"):
                    toc_id = query.get("toc_id") or "main"
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        entries = store.list_toc_entries(toc_id)
                    self._send_json({"toc_id": toc_id, "entries": entries}, HTTPStatus.OK)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "directives"):
                    try:
                        after_sequence = _optional_nonnegative_int(query.get("after_sequence"), "after_sequence")
                        limit = _optional_positive_int(query.get("limit"), "limit")
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        directives = store.list_directives(
                            channel_uuid=query.get("channel"),
                            directive_kind=query.get("directive_kind"),
                            after_sequence=after_sequence,
                            limit=limit,
                        )
                    self._send_json({"directives": [directive.to_dict() for directive in directives]}, HTTPStatus.OK)
                    return True
                return False

            def _handle_refinement_post(self) -> bool:
                parts = _path_parts(urlparse(self.path).path)
                if parts == (*REFINEMENT_PATH_PREFIX, "history-requests"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    channel_uuid = str(body.get("channel_uuid") or "")
                    if not channel_uuid:
                        self._send_json({"error": "channel_uuid_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    try:
                        request = _history_request_from_body(body, channel_uuid)
                        chunks = replay_channel_chunks(
                            ChannelJSONLHistory(owner.root),
                            owner.resolver,
                            request["channel_uuid"],
                            chunk_size=int(request["chunk_size"]),
                            limit=request["messages"],
                            direction=str(request["direction"]),
                            event_types=tuple(request["event_types"]),
                        )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    payloads = [row["payload"] for chunk in chunks for row in chunk["events"]]
                    result_ref = {
                        "chunk_count": len(chunks),
                        "event_count": sum(int(chunk["event_count"]) for chunk in chunks),
                        "payload_errors": len(
                            [payload for payload in payloads if payload.get("event_type") == "payload_error"]
                        ),
                        "resolved_payloads": len(
                            [payload for payload in payloads if payload.get("event_type") != "payload_error"]
                        ),
                    }
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        directive = store.record_directive(
                            channel_uuid=str(request["channel_uuid"]),
                            directive_kind="history_request",
                            source=str(body.get("source") or "user"),
                            visibility="raw_only",
                            target_type="channel",
                            target_id=str(request["channel_uuid"]),
                            payload=request,
                            result_ref=result_ref,
                        )
                    self._send_json(
                        {
                            "request": request,
                            "directive": directive.to_dict(),
                            "chunks": chunks,
                            "chunk_count": result_ref["chunk_count"],
                            "chunk_lengths": [chunk["event_count"] for chunk in chunks],
                            "payload_errors": result_ref["payload_errors"],
                            "resolved_payloads": result_ref["resolved_payloads"],
                        },
                        HTTPStatus.CREATED,
                    )
                    return True
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
                            self._record_refinement_directive(
                                store,
                                channel_uuid=store.channel_uuid_for_target("block", source_block_id),
                                directive_kind="create_cut",
                                body=body,
                                target_type="cut",
                                target_id=cut.cut_id,
                                result_ref={"type": "cut", "id": cut.cut_id},
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    except KeyError:
                        self._send_json({"error": "source_block_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"cut": cut.to_dict()}, HTTPStatus.CREATED)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "blocks"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    channel_uuid = str(body.get("channel_uuid") or "")
                    message_uuid = str(body.get("message_uuid") or "")
                    sender = str(body.get("sender") or "")
                    presentation = str(body.get("presentation") or "")
                    if not channel_uuid:
                        self._send_json({"error": "channel_uuid_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not message_uuid:
                        self._send_json({"error": "message_uuid_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not sender:
                        self._send_json({"error": "sender_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not presentation:
                        self._send_json({"error": "presentation_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    raw_refs = body.get("raw_refs")
                    metadata = body.get("metadata")
                    if raw_refs is None:
                        raw_refs = ()
                    if not isinstance(raw_refs, list):
                        self._send_json({"error": "raw_refs_must_be_array"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if metadata is None:
                        metadata = {}
                    if not isinstance(metadata, dict):
                        self._send_json({"error": "metadata_must_be_object"}, HTTPStatus.BAD_REQUEST)
                        return True
                    block = ConversationBlock(
                        block_id=str(body.get("block_id") or f"message:{message_uuid}"),
                        channel_uuid=channel_uuid,
                        message_uuid=message_uuid,
                        sender=sender,
                        sender_type=str(body.get("sender_type") or "user"),
                        created_at=str(body.get("created_at") or ""),
                        payload_kind=str(body.get("payload_kind") or "text"),
                        presentation=presentation,
                        raw_refs=tuple(raw_refs),
                        metadata=metadata,
                    )
                    with ConversationRefinementStore(owner.refinement_db_path) as store:
                        stored_block = store.add_block(block)
                        self._record_refinement_directive(
                            store,
                            channel_uuid=stored_block.channel_uuid,
                            directive_kind="adopt_block",
                            body=body,
                            target_type="block",
                            target_id=stored_block.block_id,
                            result_ref={"type": "block", "id": stored_block.block_id},
                        )
                    self._send_json({"block": stored_block.to_dict()}, HTTPStatus.CREATED)
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
                            self._record_refinement_directive(
                                store,
                                channel_uuid=store.channel_uuid_for_target("chapter", chapter.chapter_id),
                                directive_kind="create_chapter",
                                body=body,
                                target_type="chapter",
                                target_id=chapter.chapter_id,
                                result_ref={"type": "chapter", "id": chapter.chapter_id},
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
                            self._record_refinement_directive(
                                store,
                                channel_uuid=store.channel_uuid_for_target(target_type, target_id),
                                directive_kind="create_bookmark",
                                body=body,
                                target_type="bookmark",
                                target_id=bookmark.bookmark_id,
                                result_ref={"type": "bookmark", "id": bookmark.bookmark_id},
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
                            self._record_refinement_directive(
                                store,
                                channel_uuid=store.channel_uuid_for_target(source_type, source_id),
                                directive_kind="quote_span",
                                body=body,
                                target_type="quote",
                                target_id=quote.quote_id,
                                result_ref={"type": "quote", "id": quote.quote_id},
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
                            self._record_refinement_directive(
                                store,
                                channel_uuid=store.channel_uuid_for_target(target_type, target_id),
                                directive_kind="pin_sticky",
                                body=body,
                                target_type="sticky",
                                target_id=sticky.sticky_id,
                                result_ref={"type": "sticky", "id": sticky.sticky_id},
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    except KeyError:
                        self._send_json({"error": "sticky_target_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"sticky": sticky.to_dict()}, HTTPStatus.CREATED)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "edits"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    input_ref = body.get("input_ref")
                    output_ref = body.get("output_ref")
                    diff_content = body.get("diff_content")
                    source_ref = body.get("source_ref")
                    edit_type = str(body.get("edit_type") or "")
                    author = str(body.get("author") or "")
                    if not isinstance(input_ref, dict):
                        self._send_json({"error": "input_ref_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not isinstance(output_ref, dict):
                        self._send_json({"error": "output_ref_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not edit_type:
                        self._send_json({"error": "edit_type_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not author:
                        self._send_json({"error": "author_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not isinstance(diff_content, dict):
                        self._send_json({"error": "diff_content_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if source_ref is not None and not isinstance(source_ref, dict):
                        self._send_json({"error": "source_ref_must_be_object"}, HTTPStatus.BAD_REQUEST)
                        return True
                    diff_uri = body.get("diff_uri")
                    if diff_uri is not None and not isinstance(diff_uri, str):
                        self._send_json({"error": "diff_uri_must_be_string"}, HTTPStatus.BAD_REQUEST)
                        return True
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            edit, diff = store.apply_edit(
                                input_ref=input_ref,
                                output_ref=output_ref,
                                edit_type=edit_type,
                                reason=str(body.get("reason") or ""),
                                author=author,
                                diff_content=diff_content,
                                source_ref=source_ref,
                                diff_format=str(body.get("diff_format") or "jsondiff"),
                                diff_uri=diff_uri,
                            )
                            self._record_refinement_directive(
                                store,
                                channel_uuid=store.channel_uuid_for_ref(input_ref),
                                directive_kind="apply_edit",
                                body=body,
                                target_type="edit",
                                target_id=edit.edit_id,
                                result_ref={"type": "edit", "id": edit.edit_id, "diff_id": diff.diff_id},
                            )
                    except KeyError:
                        self._send_json({"error": "edit_target_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"edit": edit.to_dict(), "diff": diff.to_dict()}, HTTPStatus.CREATED)
                    return True
                if parts == (*REFINEMENT_PATH_PREFIX, "toc"):
                    body = self._read_json_body()
                    if body is None:
                        return True
                    entry_type = str(body.get("entry_type") or "")
                    target_id = str(body.get("target_id") or "")
                    title = str(body.get("title") or "")
                    source_scope = body.get("source_scope")
                    if not entry_type:
                        self._send_json({"error": "entry_type_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not target_id:
                        self._send_json({"error": "target_id_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if not title:
                        self._send_json({"error": "title_required"}, HTTPStatus.BAD_REQUEST)
                        return True
                    if source_scope is not None and not isinstance(source_scope, dict):
                        self._send_json({"error": "source_scope_must_be_object"}, HTTPStatus.BAD_REQUEST)
                        return True
                    try:
                        ordinal = _positive_int_value(body.get("ordinal"), "ordinal", allow_zero=True)
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            channel_uuid = store.channel_uuid_for_target(entry_type, target_id)
                            entry = store.add_toc_entry(
                                toc_id=str(body.get("toc_id") or "main"),
                                ordinal=ordinal,
                                entry_type=entry_type,
                                target_id=target_id,
                                title=title,
                                source_scope=source_scope,
                            )
                            self._record_refinement_directive(
                                store,
                                channel_uuid=channel_uuid,
                                directive_kind="add_toc_entry",
                                body=body,
                                target_type="toc_entry",
                                target_id=f"{entry['toc_id']}:{entry['ordinal']}",
                                result_ref={
                                    "type": "toc_entry",
                                    "toc_id": entry["toc_id"],
                                    "ordinal": entry["ordinal"],
                                },
                            )
                    except ValueError as error:
                        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                        return True
                    except KeyError:
                        self._send_json({"error": "toc_target_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"entry": entry}, HTTPStatus.CREATED)
                    return True
                return False

            def _handle_refinement_delete(self) -> bool:
                parts = _path_parts(urlparse(self.path).path)
                if len(parts) == 4 and tuple(parts[:3]) == (*REFINEMENT_PATH_PREFIX, "cuts"):
                    cut_id = parts[3]
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            channel_uuid = store.channel_uuid_for_target("cut", cut_id)
                            cut = store.remove_cut(cut_id)
                            self._record_refinement_directive(
                                store,
                                channel_uuid=channel_uuid,
                                directive_kind="remove_cut",
                                body={},
                                target_type="cut",
                                target_id=cut.cut_id,
                                result_ref={"type": "cut", "id": cut.cut_id, "status": cut.status},
                            )
                    except KeyError:
                        self._send_json({"error": "cut_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"cut": cut.to_dict()}, HTTPStatus.OK)
                    return True
                if len(parts) == 4 and tuple(parts[:3]) == (*REFINEMENT_PATH_PREFIX, "stickies"):
                    sticky_id = parts[3]
                    try:
                        with ConversationRefinementStore(owner.refinement_db_path) as store:
                            channel_uuid = store.channel_uuid_for_target(
                                "sticky",
                                sticky_id,
                            )
                            sticky = store.clear_sticky(sticky_id)
                            self._record_refinement_directive(
                                store,
                                channel_uuid=channel_uuid,
                                directive_kind="clear_sticky",
                                body={},
                                target_type="sticky",
                                target_id=sticky.sticky_id,
                                result_ref={"type": "sticky", "id": sticky.sticky_id, "status": sticky.clear_state},
                            )
                    except KeyError:
                        self._send_json({"error": "sticky_not_found"}, HTTPStatus.NOT_FOUND)
                        return True
                    self._send_json({"sticky": sticky.to_dict()}, HTTPStatus.OK)
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
                allowed_origin = owner.cors_origin_for(self.headers.get("Origin"))
                if allowed_origin is not None:
                    self.send_header("Access-Control-Allow-Origin", allowed_origin)
                if owner.allowed_origins:
                    self.send_header("Vary", "Origin")
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
            f"{server.base_url}/alienhand/refinement/search?q=workbench&channel={channel_uuid}",
            access_token=access_token,
        )
        unauthorized_response = _http_json(f"{server.base_url}/alienhand/refinement/blocks")
        history_request_response = _http_post_json(
            f"{server.base_url}/alienhand/refinement/history-requests",
            {"channel_uuid": channel_uuid, "chunk_size": 1, "messages": 1},
            access_token=access_token,
        )
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
        chapter_id = chapter_response["json"].get("chapter", {}).get("chapter_id")
        edit_response = _http_post_json(
            f"{server.base_url}/alienhand/refinement/edits",
            {
                "input_ref": {"type": "chapter", "id": chapter_id},
                "output_ref": {"type": "chapter", "id": chapter_id, "revision": 1},
                "edit_type": "annotate",
                "reason": "Prove editorial API.",
                "author": "alienhand",
                "diff_content": {"add": [{"path": "/summary", "value": "Editorial API proof."}]},
            },
            access_token=access_token,
        )
        edit_id = edit_response["json"].get("edit", {}).get("edit_id")
        diff_id = edit_response["json"].get("diff", {}).get("diff_id")
        toc_response = _http_post_json(
            f"{server.base_url}/alienhand/refinement/toc",
            {
                "toc_id": "main",
                "ordinal": 0,
                "entry_type": "chapter",
                "target_id": chapter_id,
                "title": "Workbench Sources",
                "source_scope": {"block_ids": [imported_blocks[0].block_id], "cut_ids": [cut_id]},
            },
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
        edits_response = _http_json(
            f"{server.base_url}/alienhand/refinement/edits?edit_type=annotate",
            access_token=access_token,
        )
        diffs_response = _http_json(
            f"{server.base_url}/alienhand/refinement/edit-diffs?edit_id={edit_id}",
            access_token=access_token,
        )
        diff_response = _http_json(
            f"{server.base_url}/alienhand/refinement/edit-diffs/{diff_id}",
            access_token=access_token,
        )
        toc_list_response = _http_json(
            f"{server.base_url}/alienhand/refinement/toc?toc_id=main",
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
            f"{server.base_url}/alienhand/refinement/stickies?channel={channel_uuid}&clear_state=active",
            access_token=access_token,
        )
        clear_sticky_response = _http_delete_json(
            f"{server.base_url}/alienhand/refinement/stickies/{sticky_response['json'].get('sticky', {}).get('sticky_id')}",
            access_token=access_token,
        )
        active_stickies_after_clear_response = _http_json(
            f"{server.base_url}/alienhand/refinement/stickies?channel={channel_uuid}&clear_state=active",
            access_token=access_token,
        )
        directives_response = _http_json(
            f"{server.base_url}/alienhand/refinement/directives?channel={channel_uuid}",
            access_token=access_token,
        )
        directives_after_first_response = _http_json(
            f"{server.base_url}/alienhand/refinement/directives?channel={channel_uuid}&after_sequence=1",
            access_token=access_token,
        )
        directive_limit_response = _http_json(
            f"{server.base_url}/alienhand/refinement/directives?channel={channel_uuid}&limit=3",
            access_token=access_token,
        )
        create_cut_directives_response = _http_json(
            f"{server.base_url}/alienhand/refinement/directives?channel={channel_uuid}&directive_kind=create_cut",
            access_token=access_token,
        )
        base_url = server.base_url

    blocks = blocks_response["json"].get("blocks", [])
    hits = search_response["json"].get("hits", [])
    cuts = cuts_response["json"].get("cuts", [])
    chapters = chapters_response["json"].get("chapters", [])
    edits = edits_response["json"].get("edits", [])
    diffs = diffs_response["json"].get("diffs", [])
    toc_entries = toc_list_response["json"].get("entries", [])
    bookmarks = bookmarks_response["json"].get("bookmarks", [])
    quotes = quotes_response["json"].get("quotes", [])
    stickies = stickies_response["json"].get("stickies", [])
    active_stickies_after_clear = active_stickies_after_clear_response["json"].get("stickies", [])
    directives = directives_response["json"].get("directives", [])
    directives_after_first = directives_after_first_response["json"].get("directives", [])
    limited_directives = directive_limit_response["json"].get("directives", [])
    create_cut_directives = create_cut_directives_response["json"].get("directives", [])
    directive_sequences = [directive.get("sequence") for directive in directives]
    history_request = history_request_response["json"]
    refinement_http_ok = (
        blocks_response["status"] == 200
        and len(blocks) == 1
        and blocks[0].get("block_id") == imported_blocks[0].block_id
        and search_response["status"] == 200
        and [hit.get("block_id") for hit in hits] == [imported_blocks[0].block_id]
        and unauthorized_response["status"] == 401
        and history_request_response["status"] == 201
        and history_request.get("chunk_lengths") == [1]
        and history_request.get("resolved_payloads") == 1
        and history_request.get("directive", {}).get("visibility") == "raw_only"
        and cut_response["status"] == 201
        and chapter_response["status"] == 201
        and edit_response["status"] == 201
        and edit_response["json"].get("edit", {}).get("diff_id") == diff_id
        and toc_response["status"] == 201
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
        and chapters[0].get("edit_chain") == [edit_id]
        and len(edits) == 1
        and edits[0].get("edit_id") == edit_id
        and len(diffs) == 1
        and diffs[0].get("diff_id") == diff_id
        and diff_response["json"].get("diff", {}).get("diff_id") == diff_id
        and len(toc_entries) == 1
        and toc_entries[0].get("target_id") == chapter_id
        and len(bookmarks) == 1
        and bookmarks[0].get("target_id") == imported_blocks[0].block_id
        and len(quotes) == 1
        and quotes[0].get("source_id") == imported_blocks[0].block_id
        and len(stickies) == 1
        and clear_sticky_response["status"] == 200
        and clear_sticky_response["json"].get("sticky", {}).get("clear_state") == "dismissed"
        and active_stickies_after_clear == []
        and directives_response["status"] == 200
        and directives_after_first_response["status"] == 200
        and directive_limit_response["status"] == 200
        and create_cut_directives_response["status"] == 200
        and directive_sequences == list(range(1, 11))
        and len(directives_after_first) == 9
        and len(limited_directives) == 3
        and [directive.get("target_id") for directive in create_cut_directives] == [cut_id]
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
        "history_request_chunks": history_request.get("chunk_count"),
        "history_request_directive_kind": history_request.get("directive", {}).get("directive_kind"),
        "created_cut_id": cut_id,
        "created_chapter_id": chapter_id,
        "created_edit_id": edit_id,
        "created_diff_id": diff_id,
        "listed_edits": len(edits),
        "listed_diffs": len(diffs),
        "listed_toc_entries": len(toc_entries),
        "created_bookmark_id": bookmark_response["json"].get("bookmark", {}).get("bookmark_id"),
        "bookmark_note": bookmark_response["json"].get("bookmark", {}).get("note"),
        "created_quote_id": quote_response["json"].get("quote", {}).get("quote_id"),
        "quote_excerpt": quote_response["json"].get("quote", {}).get("excerpt"),
        "created_sticky_id": sticky_response["json"].get("sticky", {}).get("sticky_id"),
        "sticky_target_type": sticky_response["json"].get("sticky", {}).get("target_type"),
        "cleared_sticky_state": clear_sticky_response["json"].get("sticky", {}).get("clear_state"),
        "removed_cut_status": remove_response["json"].get("cut", {}).get("status"),
        "listed_cuts": len(cuts),
        "listed_chapters": len(chapters),
        "listed_bookmarks": len(bookmarks),
        "listed_quotes": len(quotes),
        "listed_stickies": len(stickies),
        "active_stickies_after_clear": len(active_stickies_after_clear),
        "listed_directives": len(directives),
        "directive_sequences": directive_sequences,
        "directives_after_first": len(directives_after_first),
        "limited_directives": len(limited_directives),
        "create_cut_directives": len(create_cut_directives),
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
        render_response = _http_json(
            render_url,
            access_token=service.payload_resolver_token,
            origin=thelounge_base_url,
        )
        index_response = _http_text(f"{thelounge_base_url}/")

    replayed = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    resolved_payloads = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]
    index_html = index_response["text"]
    render_row = render_response["json"]
    resolver_data_attribute = f'data-alienhand-payload-resolver="{resolver_base_url}"'
    resolver_token_attribute = f'data-alienhand-payload-resolver-token="{service.payload_resolver_token}"'
    thelounge_html_has_resolver = resolver_data_attribute in index_html
    thelounge_html_has_resolver_token = resolver_token_attribute in index_html
    resolver_cors_origin_ok = render_response["headers"].get("Access-Control-Allow-Origin") == thelounge_base_url
    fetch_ok = (
        render_response["status"] == 200
        and render_row.get("status") == "resolved"
        and render_row.get("message_uuid") == published.envelope.message_uuid
        and render_row.get("content", {}).get("text") == text
        and resolver_cors_origin_ok
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
        "payload_resolver_cors_origin_ok": resolver_cors_origin_ok,
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
            f"{resolver_base_url}/alienhand/refinement/search?q=workbench&channel={channel_uuid}",
            access_token=access_token,
        )
        history_request_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/history-requests",
            {"channel_uuid": channel_uuid, "chunk_size": 1, "messages": 1},
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
        chapter_id = chapter_response["json"].get("chapter", {}).get("chapter_id")
        chapter_bookmark_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/bookmarks",
            {
                "target_type": "chapter",
                "target_id": chapter_id,
                "label": "Runtime chapter bookmark",
                "note": "Chapter bookmark follows the refined product.",
            },
            access_token=access_token,
        )
        chapter_quote_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/quotes",
            {
                "source_type": "chapter",
                "source_id": chapter_id,
                "excerpt": "Runtime workbench proof chapter.",
                "provenance": {"chapter_id": chapter_id, "cut_ids": [cut_id]},
            },
            access_token=access_token,
        )
        chapter_sticky_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/stickies",
            {"target_type": "chapter", "target_id": chapter_id},
            access_token=access_token,
        )
        edit_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/edits",
            {
                "input_ref": {"type": "chapter", "id": chapter_id},
                "output_ref": {"type": "chapter", "id": chapter_id, "revision": 1},
                "edit_type": "annotate",
                "reason": "Runtime workbench editorial proof.",
                "author": "alienhand",
                "diff_content": {"add": [{"path": "/summary", "value": "Runtime editorial proof."}]},
            },
            access_token=access_token,
        )
        edit_id = edit_response["json"].get("edit", {}).get("edit_id")
        diff_id = edit_response["json"].get("diff", {}).get("diff_id")
        toc_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/toc",
            {
                "entry_type": "chapter",
                "ordinal": 0,
                "source_scope": {"block_ids": [block_id], "cut_ids": [cut_id]},
                "target_id": chapter_id,
                "title": "Workbench Runtime",
                "toc_id": "main",
            },
            access_token=access_token,
        )
        live_block_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/blocks",
            {
                "block_id": f"thelounge:{channel_uuid}:1",
                "channel_uuid": channel_uuid,
                "created_at": "2026-05-13T13:57:00.000Z",
                "message_uuid": f"thelounge-{channel_uuid}-1",
                "metadata": {"source": "runtime_live_source"},
                "payload_kind": "irc_text",
                "presentation": "Runtime live IRC source.",
                "raw_refs": [{"kind": "thelounge_message", "message_id": 1}],
                "sender": "alienhanduser00",
                "sender_type": "user",
            },
            access_token=access_token,
        )
        live_cut_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/cuts",
            {"position": 1, "source_block_id": live_block_response["json"].get("block", {}).get("block_id")},
            access_token=access_token,
        )
        live_block_id = live_block_response["json"].get("block", {}).get("block_id")
        live_cut_id = live_cut_response["json"].get("cut", {}).get("cut_id")
        block_bookmark_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/bookmarks",
            {
                "target_type": "block",
                "target_id": live_block_id,
                "label": "Runtime source bookmark",
                "note": "Source bookmark remains rooted in raw conversation.",
            },
            access_token=access_token,
        )
        block_quote_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/quotes",
            {
                "source_type": "block",
                "source_id": live_block_id,
                "excerpt": "Runtime live IRC source.",
                "provenance": {"block_id": live_block_id},
            },
            access_token=access_token,
        )
        block_sticky_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/stickies",
            {"target_type": "block", "target_id": live_block_id},
            access_token=access_token,
        )
        cut_bookmark_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/bookmarks",
            {
                "target_type": "cut",
                "target_id": live_cut_id,
                "label": "Runtime cut bookmark",
                "note": "Cut bookmark follows the selected excerpt.",
            },
            access_token=access_token,
        )
        cut_quote_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/quotes",
            {
                "source_type": "cut",
                "source_id": live_cut_id,
                "excerpt": "Runtime live IRC source cut.",
                "provenance": {"cut_id": live_cut_id, "source_block_id": live_block_id},
            },
            access_token=access_token,
        )
        cut_sticky_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/stickies",
            {"target_type": "cut", "target_id": live_cut_id},
            access_token=access_token,
        )
        bookmark_sticky_response = _http_post_json(
            f"{resolver_base_url}/alienhand/refinement/stickies",
            {
                "target_type": "bookmark",
                "target_id": block_bookmark_response["json"].get("bookmark", {}).get("bookmark_id"),
            },
            access_token=access_token,
        )
        cut_sticky_id = cut_sticky_response["json"].get("sticky", {}).get("sticky_id")
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
        live_blocks_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/blocks?channel={channel_uuid}",
            access_token=access_token,
        )
        edits_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/edits?edit_type=annotate",
            access_token=access_token,
        )
        diffs_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/edit-diffs?edit_id={edit_id}",
            access_token=access_token,
        )
        diff_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/edit-diffs/{diff_id}",
            access_token=access_token,
        )
        toc_list_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/toc?toc_id=main",
            access_token=access_token,
        )
        bookmarks_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/bookmarks?channel={channel_uuid}",
            access_token=access_token,
        )
        cut_bookmarks_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/bookmarks?channel={channel_uuid}&target_type=cut",
            access_token=access_token,
        )
        quotes_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/quotes?channel={channel_uuid}",
            access_token=access_token,
        )
        cut_quotes_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/quotes?channel={channel_uuid}&source_type=cut",
            access_token=access_token,
        )
        stickies_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/stickies?channel={channel_uuid}&clear_state=active",
            access_token=access_token,
        )
        clear_cut_sticky_response = _http_delete_json(
            f"{resolver_base_url}/alienhand/refinement/stickies/{cut_sticky_id}",
            access_token=access_token,
        )
        active_stickies_after_clear_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/stickies?channel={channel_uuid}&clear_state=active",
            access_token=access_token,
        )
        directives_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/directives?channel={channel_uuid}",
            access_token=access_token,
        )
        create_cut_directives_response = _http_json(
            f"{resolver_base_url}/alienhand/refinement/directives?channel={channel_uuid}&directive_kind=create_cut",
            access_token=access_token,
        )
        render_response = _http_json(
            service.payload_http_server.render_url(published.envelope.message_uuid),
            access_token=access_token,
            origin=thelounge_base_url,
        )
        index_response = _http_text(f"{thelounge_base_url}/")
        bundle_response = _http_text(f"{thelounge_base_url}/js/bundle.js")
        style_response = _http_text(f"{thelounge_base_url}/css/style.css")
        ergo_port = service.port
        resolved_thelounge_port = service.thelounge_port

    replayed = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    resolved_payloads = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]
    render_row = render_response["json"]
    history_request = history_request_response["json"]
    search_hits = search_response["json"].get("hits", [])
    live_blocks = live_blocks_response["json"].get("blocks", [])
    cuts = cuts_response["json"].get("cuts", [])
    active_cuts = active_cuts_response["json"].get("cuts", [])
    chapters = chapters_response["json"].get("chapters", [])
    edits = edits_response["json"].get("edits", [])
    diffs = diffs_response["json"].get("diffs", [])
    toc_entries = toc_list_response["json"].get("entries", [])
    bookmarks = bookmarks_response["json"].get("bookmarks", [])
    cut_bookmarks = cut_bookmarks_response["json"].get("bookmarks", [])
    quotes = quotes_response["json"].get("quotes", [])
    cut_quotes = cut_quotes_response["json"].get("quotes", [])
    stickies = stickies_response["json"].get("stickies", [])
    active_stickies_after_clear = active_stickies_after_clear_response["json"].get("stickies", [])
    directives = directives_response["json"].get("directives", [])
    create_cut_directives = create_cut_directives_response["json"].get("directives", [])
    directive_sequences = [directive.get("sequence") for directive in directives]
    index_html = index_response["text"]
    bundle_js = bundle_response["text"]
    style_css = style_response["text"]
    resolver_data_attribute = f'data-alienhand-payload-resolver="{resolver_base_url}"'
    workbench_bundle_markers = {
        "workbench aria label": "AlienHand refinement workbench",
        "chat toggle aria label": "Show or hide Chat frame",
        "chat frame toggle": "Chat",
        "history action": "Load history",
        "history loading status": "Loading channel history.",
        "history loaded status": "Loaded history:",
        "cutting toggle aria label": "Show or hide Cutting frame",
        "cutting frame toggle": "Cutting",
        "editing toggle aria label": "Show or hide Editing frame",
        "editing frame toggle": "Editing",
        "insertion bridge": "Cut insertion bridge",
        "insert aria label": "Insert selected source at current Cutting position",
        "keyboard insertion up": "Move cut insertion up",
        "keyboard insertion down": "Move cut insertion down",
        "bridge instruction": "Select a chat line with its arrow",
        "bridge blocked status": "Bridge blocked: select a chat line",
        "empty cuts guidance": "No cuts yet. Select a chat line",
        "refreshing status": "Refreshing refinement state.",
        "error status": "Latest workbench error is shown above.",
        "directive ledger status": "directive ledger events",
        "empty directive ledger status": "No directive ledger events for this channel.",
        "directive sync saw status": "Directive ledger sync saw",
        "directive sync waiting status": "Directive ledger sync waiting",
        "removed cuts detail": "removed cuts",
        "missing source label": "Missing source block",
        "missing source reveal status": "Revealed cut; source block is missing",
        "chapter action": "Create chapter",
        "quote excerpt input": "Quote excerpt",
        "quote action": "Quote",
        "pinned reminder chip": "Pinned reminder",
        "unpin action": "Unpin",
        "edit action": "Apply edit",
        "editing source line label": "Editing source line",
        "removed editing source line label": "Removed editing source line",
        "hidden source reveal status": "Chat and Cutting are hidden",
        "toc action": "Add TOC",
        "source direct insertion action": "Insert this message into cuts at the current Cutting position",
        "source direct insertion event": "alienhand:source-message:insert-requested",
        "chapter directory selection status": "Selected chapter has no currently visible source cut to reveal.",
        "chapter directory item class": "alienhand-workbench__directory-item",
        "workbench class": "alienhand-workbench",
    }
    workbench_style_markers = {
        "workbench class": "alienhand-workbench",
        "status surface": "alienhand-workbench__status",
        "bookmarks": "alienhand-workbench__bookmarks",
        "quotes": "alienhand-workbench__quotes",
        "quote form": "alienhand-workbench__quote-form",
        "stickies": "alienhand-workbench__stickies",
        "edits": "alienhand-workbench__edits",
        "toc": "alienhand-workbench__toc",
        "source arrow": "alienhand-source-arrow",
        "bridge rail": "alienhand-workbench__bridge-rail",
        "bridge arrow": "alienhand-workbench__bridge-arrow",
        "bridge step": "alienhand-workbench__bridge-step",
        "blocked status pill": "alienhand-workbench__status-pill--blocked",
        "removed cuts": "alienhand-workbench__removed-cuts",
        "stale source": "alienhand-workbench__stale-source",
        "removed editing line": "alienhand-workbench__editing-line--removed",
        "directory item": "alienhand-workbench__directory-item",
    }
    missing_bundle_markers = [
        name for name, marker in workbench_bundle_markers.items() if marker not in bundle_js
    ]
    missing_style_markers = [
        name for name, marker in workbench_style_markers.items() if marker not in style_css
    ]
    workbench_bundle_ok = not missing_bundle_markers
    workbench_style_ok = not missing_style_markers
    bookmark_target_pairs = {(row.get("target_type"), row.get("target_id")) for row in bookmarks}
    quote_source_pairs = {(row.get("source_type"), row.get("source_id")) for row in quotes}
    sticky_target_pairs = {(row.get("target_type"), row.get("target_id")) for row in stickies}
    active_sticky_ids_after_clear = {row.get("sticky_id") for row in active_stickies_after_clear}
    refinement_api_ok = (
        blocks_response["status"] == 200
        and len(blocks) == 1
        and blocks[0].get("message_uuid") == published.envelope.message_uuid
        and search_response["status"] == 200
        and [hit.get("block_id") for hit in search_hits] == [block_id]
        and history_request_response["status"] == 201
        and history_request.get("chunk_lengths") == [1]
        and history_request.get("directive", {}).get("directive_kind") == "history_request"
        and cut_response["status"] == 201
        and chapter_response["status"] == 201
        and chapter_bookmark_response["status"] == 201
        and chapter_quote_response["status"] == 201
        and chapter_sticky_response["status"] == 201
        and edit_response["status"] == 201
        and edit_response["json"].get("edit", {}).get("diff_id") == diff_id
        and toc_response["status"] == 201
        and live_block_response["status"] == 201
        and live_cut_response["status"] == 201
        and live_cut_response["json"].get("cut", {}).get("source_block_id")
        == live_block_response["json"].get("block", {}).get("block_id")
        and block_bookmark_response["status"] == 201
        and block_quote_response["status"] == 201
        and block_sticky_response["status"] == 201
        and cut_bookmark_response["status"] == 201
        and cut_quote_response["status"] == 201
        and cut_sticky_response["status"] == 201
        and bookmark_sticky_response["status"] == 201
        and remove_response["status"] == 200
        and remove_response["json"].get("cut", {}).get("status") == "removed"
        and len(live_blocks) == 2
        and len(cuts) == 2
        and len(active_cuts) == 1
        and len(chapters) == 1
        and chapters[0].get("edit_chain") == [edit_id]
        and len(edits) == 1
        and edits[0].get("edit_id") == edit_id
        and len(diffs) == 1
        and diffs[0].get("diff_id") == diff_id
        and diff_response["json"].get("diff", {}).get("diff_id") == diff_id
        and len(toc_entries) == 1
        and toc_entries[0].get("target_id") == chapter_id
        and bookmarks_response["status"] == 200
        and cut_bookmarks_response["status"] == 200
        and bookmark_target_pairs == {
            ("block", live_block_id),
            ("chapter", chapter_id),
            ("cut", live_cut_id),
        }
        and [row.get("target_id") for row in cut_bookmarks] == [live_cut_id]
        and quotes_response["status"] == 200
        and cut_quotes_response["status"] == 200
        and quote_source_pairs == {
            ("block", live_block_id),
            ("chapter", chapter_id),
            ("cut", live_cut_id),
        }
        and [row.get("source_id") for row in cut_quotes] == [live_cut_id]
        and stickies_response["status"] == 200
        and sticky_target_pairs == {
            ("block", live_block_id),
            ("bookmark", block_bookmark_response["json"].get("bookmark", {}).get("bookmark_id")),
            ("chapter", chapter_id),
            ("cut", live_cut_id),
        }
        and clear_cut_sticky_response["status"] == 200
        and clear_cut_sticky_response["json"].get("sticky", {}).get("clear_state") == "dismissed"
        and cut_sticky_id not in active_sticky_ids_after_clear
        and len(active_stickies_after_clear) == 3
        and directives_response["status"] == 200
        and create_cut_directives_response["status"] == 200
        and directive_sequences == list(range(1, 20))
        and [directive.get("target_id") for directive in create_cut_directives] == [cut_id, live_cut_id]
    )
    render_fetch_ok = (
        render_response["status"] == 200
        and render_row.get("status") == "resolved"
        and render_row.get("message_uuid") == published.envelope.message_uuid
        and render_response["headers"].get("Access-Control-Allow-Origin") == thelounge_base_url
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
        "payload_resolver_cors_origin_ok": render_response["headers"].get("Access-Control-Allow-Origin")
        == thelounge_base_url,
        "listed_blocks": len(blocks),
        "listed_blocks_after_live_source": len(live_blocks),
        "search_hits": len(search_hits),
        "history_request_chunks": history_request.get("chunk_count"),
        "history_request_directive_kind": history_request.get("directive", {}).get("directive_kind"),
        "created_cut_id": cut_id,
        "created_live_block_id": live_block_response["json"].get("block", {}).get("block_id"),
        "created_live_cut_id": live_cut_response["json"].get("cut", {}).get("cut_id"),
        "created_chapter_id": chapter_id,
        "created_edit_id": edit_id,
        "created_diff_id": diff_id,
        "created_block_bookmark_id": block_bookmark_response["json"].get("bookmark", {}).get("bookmark_id"),
        "created_cut_bookmark_id": cut_bookmark_response["json"].get("bookmark", {}).get("bookmark_id"),
        "created_chapter_bookmark_id": chapter_bookmark_response["json"].get("bookmark", {}).get("bookmark_id"),
        "created_block_quote_id": block_quote_response["json"].get("quote", {}).get("quote_id"),
        "created_cut_quote_id": cut_quote_response["json"].get("quote", {}).get("quote_id"),
        "created_chapter_quote_id": chapter_quote_response["json"].get("quote", {}).get("quote_id"),
        "created_block_sticky_id": block_sticky_response["json"].get("sticky", {}).get("sticky_id"),
        "created_cut_sticky_id": cut_sticky_id,
        "created_chapter_sticky_id": chapter_sticky_response["json"].get("sticky", {}).get("sticky_id"),
        "created_bookmark_sticky_id": bookmark_sticky_response["json"].get("sticky", {}).get("sticky_id"),
        "removed_cut_status": remove_response["json"].get("cut", {}).get("status"),
        "listed_cuts": len(cuts),
        "active_cuts_after_remove": len(active_cuts),
        "listed_chapters": len(chapters),
        "listed_edits": len(edits),
        "listed_diffs": len(diffs),
        "listed_toc_entries": len(toc_entries),
        "listed_bookmarks": len(bookmarks),
        "listed_cut_bookmarks": len(cut_bookmarks),
        "listed_quotes": len(quotes),
        "listed_cut_quotes": len(cut_quotes),
        "listed_stickies_before_clear": len(stickies),
        "active_stickies_after_cut_clear": len(active_stickies_after_clear),
        "cleared_cut_sticky_state": clear_cut_sticky_response["json"].get("sticky", {}).get("clear_state"),
        "listed_directives": len(directives),
        "directive_sequences": directive_sequences,
        "create_cut_directives": len(create_cut_directives),
        "workbench_bundle_ok": workbench_bundle_ok,
        "missing_workbench_bundle_markers": missing_bundle_markers,
        "workbench_style_ok": workbench_style_ok,
        "missing_workbench_style_markers": missing_style_markers,
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


def _optional_nonnegative_int(value: str | None, name: str) -> int | None:
    if value is None or value == "":
        return None
    return _positive_int_value(value, name, allow_zero=True)


def _history_request_from_body(body: JsonDict, channel_uuid: str) -> JsonDict:
    mode = str(body.get("mode") or ("all_messages" if body.get("all") is True else "last_messages"))
    if mode not in {"last_messages", "all_messages"}:
        raise ValueError("history request mode must be last_messages or all_messages")

    chunk_size = _positive_int_value(
        body.get("chunk_size") or DEFAULT_HISTORY_COMMAND_CHUNK_SIZE,
        "chunk_size",
    )
    if chunk_size > MAX_HISTORY_COMMAND_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be at most {MAX_HISTORY_COMMAND_CHUNK_SIZE}")

    messages: int | None
    if mode == "all_messages":
        messages = None
    else:
        messages = _positive_int_value(
            body.get("messages") or DEFAULT_HISTORY_COMMAND_MESSAGES,
            "messages",
        )
        if messages > MAX_HISTORY_COMMAND_MESSAGES:
            raise ValueError(f"messages must be at most {MAX_HISTORY_COMMAND_MESSAGES}")

    return {
        "channel_uuid": normalize_channel_uuid(channel_uuid),
        "chunk_size": chunk_size,
        "direction": RECENT_FIRST_BACKFILL,
        "event_types": ["message"],
        "messages": messages,
        "mode": mode,
    }


def _positive_int_value(value: Any, name: str, *, allow_zero: bool = False) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    minimum = 0 if allow_zero else 1
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed


def _http_json(
    url: str,
    *,
    access_token: str | None = None,
    origin: str = "http://127.0.0.1",
) -> JsonDict:
    headers = {"Accept": "application/json", "Origin": origin}
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


def _http_post_json(
    url: str,
    body: JsonDict,
    *,
    access_token: str | None = None,
    origin: str = "http://127.0.0.1",
) -> JsonDict:
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Origin": origin}
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


def _http_delete_json(
    url: str,
    *,
    access_token: str | None = None,
    origin: str = "http://127.0.0.1",
) -> JsonDict:
    headers = {"Accept": "application/json", "Origin": origin}
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


def _http_options(url: str, *, origin: str = "http://127.0.0.1") -> JsonDict:
    request = Request(url, headers={"Origin": origin}, method="OPTIONS")
    with urlopen(request, timeout=5.0) as response:
        response.read()
        return {"headers": dict(response.headers), "status": response.status}


def _http_text(url: str) -> JsonDict:
    request = Request(url)
    with urlopen(request, timeout=5.0) as response:
        return {"text": response.read().decode("utf-8"), "headers": dict(response.headers), "status": response.status}
