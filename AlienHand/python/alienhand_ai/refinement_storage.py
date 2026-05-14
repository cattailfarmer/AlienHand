from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable
from uuid import uuid4


JsonDict = dict[str, Any]
SCHEMA_VERSION = 2
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
BOOKMARK_TARGET_TYPES = {"block", "cut", "chapter"}
QUOTE_SOURCE_TYPES = BOOKMARK_TARGET_TYPES
STICKY_TARGET_TYPES = {"block", "cut", "chapter", "bookmark"}


@dataclass(frozen=True)
class ConversationBlock:
    block_id: str
    channel_uuid: str
    message_uuid: str
    sender: str
    sender_type: str
    created_at: str
    payload_kind: str
    presentation: str
    raw_refs: tuple[JsonDict, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationCut:
    cut_id: str
    source_block_id: str
    position: int
    status: str = "active"
    annotations: tuple[str, ...] = ()

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationChapter:
    chapter_id: str
    title: str
    summary: str
    member_cut_ids: tuple[str, ...]
    member_block_ids: tuple[str, ...]
    bookmarks: tuple[str, ...] = ()
    quotes: tuple[str, ...] = ()
    edit_chain: tuple[str, ...] = ()

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationBookmark:
    bookmark_id: str
    target_type: str
    target_id: str
    scope: str = "both"
    label: str = ""
    note: str = ""
    persistence: str = "durable_channel"
    promotion_state: str = "mirrored"

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationSticky:
    sticky_id: str
    target_type: str
    target_id: str
    visibility: str = "visible"
    retention: str = "session"
    clear_state: str = "active"

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationQuote:
    quote_id: str
    source_type: str
    source_id: str
    excerpt: str
    provenance: JsonDict
    display_mode: str = "inline"

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationEdit:
    edit_id: str
    input_ref: JsonDict
    output_ref: JsonDict
    edit_type: str
    reason: str
    author: str
    diff_id: str

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationEditDiff:
    diff_id: str
    edit_id: str
    source_ref: JsonDict
    diff_format: str
    diff_uri: str
    content: JsonDict

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationDirective:
    sequence: int
    directive_id: str
    channel_uuid: str
    directive_kind: str
    source: str
    visibility: str
    target_type: str
    target_id: str
    payload: JsonDict
    result_ref: JsonDict
    created_at: str

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class SearchHit:
    term: str
    block_id: str
    offset: int
    chapter_id: str | None = None
    cut_id: str | None = None

    def to_dict(self) -> JsonDict:
        return asdict(self)


class ConversationRefinementStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ConversationRefinementStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def migrate(self) -> None:
        self.connection.executescript(SCHEMA_SQL)
        version = self.schema_version()
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"unsupported refinement schema version: {version}")
        if version < SCHEMA_VERSION:
            self.connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_timestamp()),
            )
        self.connection.commit()

    def schema_version(self) -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
        return int(row["version"])

    def record_directive(
        self,
        *,
        channel_uuid: str,
        directive_kind: str,
        source: str = "user",
        visibility: str = "debug_only",
        target_type: str = "",
        target_id: str = "",
        payload: JsonDict | None = None,
        result_ref: JsonDict | None = None,
        directive_id: str | None = None,
        created_at: str | None = None,
    ) -> ConversationDirective:
        resolved_directive_id = directive_id or str(uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_directives(
                    directive_id, channel_uuid, directive_kind, source, visibility,
                    target_type, target_id, payload_json, result_ref_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_directive_id,
                    channel_uuid,
                    directive_kind,
                    source,
                    visibility,
                    target_type,
                    target_id,
                    dumps(payload or {}),
                    dumps(result_ref or {}),
                    created_at or utc_timestamp(),
                ),
            )
        return self.get_directive(resolved_directive_id)

    def get_directive(self, directive_id: str) -> ConversationDirective:
        row = self._required_row("SELECT * FROM conversation_directives WHERE directive_id = ?", (directive_id,))
        return directive_from_row(row)

    def list_directives(
        self,
        *,
        channel_uuid: str | None = None,
        directive_kind: str | None = None,
        after_sequence: int | None = None,
        limit: int | None = None,
    ) -> list[ConversationDirective]:
        params: list[Any] = []
        clauses: list[str] = []
        query = "SELECT * FROM conversation_directives"
        if channel_uuid is not None:
            clauses.append("channel_uuid = ?")
            params.append(channel_uuid)
        if directive_kind is not None:
            clauses.append("directive_kind = ?")
            params.append(directive_kind)
        if after_sequence is not None:
            clauses.append("sequence > ?")
            params.append(after_sequence)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY sequence"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [directive_from_row(row) for row in rows]

    def add_block(self, block: ConversationBlock) -> ConversationBlock:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_blocks(
                    block_id, channel_uuid, message_uuid, sender, sender_type,
                    created_at, payload_kind, presentation, raw_refs_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    block.block_id,
                    block.channel_uuid,
                    block.message_uuid,
                    block.sender,
                    block.sender_type,
                    block.created_at,
                    block.payload_kind,
                    block.presentation,
                    dumps(block.raw_refs),
                    dumps(block.metadata),
                ),
            )
            self._index_block(block)
        return block

    def upsert_block(self, block: ConversationBlock) -> ConversationBlock:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_blocks(
                    block_id, channel_uuid, message_uuid, sender, sender_type,
                    created_at, payload_kind, presentation, raw_refs_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(block_id) DO UPDATE SET
                    channel_uuid = excluded.channel_uuid,
                    message_uuid = excluded.message_uuid,
                    sender = excluded.sender,
                    sender_type = excluded.sender_type,
                    created_at = excluded.created_at,
                    payload_kind = excluded.payload_kind,
                    presentation = excluded.presentation,
                    raw_refs_json = excluded.raw_refs_json,
                    metadata_json = excluded.metadata_json
                """,
                (
                    block.block_id,
                    block.channel_uuid,
                    block.message_uuid,
                    block.sender,
                    block.sender_type,
                    block.created_at,
                    block.payload_kind,
                    block.presentation,
                    dumps(block.raw_refs),
                    dumps(block.metadata),
                ),
            )
            self._index_block(block)
        return block

    def get_block(self, block_id: str) -> ConversationBlock:
        row = self._required_row("SELECT * FROM conversation_blocks WHERE block_id = ?", (block_id,))
        return block_from_row(row)

    def list_blocks(self, *, channel_uuid: str | None = None, limit: int | None = None) -> list[ConversationBlock]:
        query = "SELECT * FROM conversation_blocks"
        params: list[Any] = []
        if channel_uuid is not None:
            query += " WHERE channel_uuid = ?"
            params.append(channel_uuid)
        query += " ORDER BY created_at, block_id"
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be at least 1")
            query += " LIMIT ?"
            params.append(limit)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [block_from_row(row) for row in rows]

    def create_cut(self, source_block_id: str, *, position: int | None = None, cut_id: str | None = None) -> ConversationCut:
        self.get_block(source_block_id)
        if position is None:
            position = self.next_cut_position()
        cut = ConversationCut(cut_id=cut_id or str(uuid4()), source_block_id=source_block_id, position=position)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_cuts(cut_id, source_block_id, position, status, annotations_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cut.cut_id, cut.source_block_id, cut.position, cut.status, dumps(cut.annotations)),
            )
        return cut

    def remove_cut(self, cut_id: str) -> ConversationCut:
        with self.connection:
            self.connection.execute("UPDATE conversation_cuts SET status = 'removed' WHERE cut_id = ?", (cut_id,))
        return self.get_cut(cut_id)

    def get_cut(self, cut_id: str) -> ConversationCut:
        row = self._required_row("SELECT * FROM conversation_cuts WHERE cut_id = ?", (cut_id,))
        return cut_from_row(row)

    def list_cuts(
        self,
        *,
        status: str | None = None,
        channel_uuid: str | None = None,
    ) -> list[ConversationCut]:
        params: list[Any] = []
        query = "SELECT conversation_cuts.* FROM conversation_cuts"
        clauses: list[str] = []
        if channel_uuid is not None:
            query += " JOIN conversation_blocks ON conversation_blocks.block_id = conversation_cuts.source_block_id"
            clauses.append("conversation_blocks.channel_uuid = ?")
            params.append(channel_uuid)
        if status is not None:
            clauses.append("conversation_cuts.status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY conversation_cuts.position, conversation_cuts.cut_id"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [cut_from_row(row) for row in rows]

    def next_cut_position(self) -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM conversation_cuts").fetchone()
        return int(row["next_position"])

    def create_chapter(
        self,
        *,
        title: str,
        summary: str,
        cut_ids: Iterable[str],
        chapter_id: str | None = None,
    ) -> ConversationChapter:
        cut_tuple = tuple(cut_ids)
        if not cut_tuple:
            raise ValueError("chapter requires at least one cut")
        cuts = [self.get_cut(cut_id) for cut_id in cut_tuple]
        block_ids = tuple(cut.source_block_id for cut in cuts)
        chapter = ConversationChapter(
            chapter_id=chapter_id or str(uuid4()),
            title=title,
            summary=summary,
            member_cut_ids=cut_tuple,
            member_block_ids=block_ids,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_chapters(
                    chapter_id, title, summary, member_cut_ids_json, member_block_ids_json,
                    bookmarks_json, quotes_json, edit_chain_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chapter.chapter_id,
                    chapter.title,
                    chapter.summary,
                    dumps(chapter.member_cut_ids),
                    dumps(chapter.member_block_ids),
                    dumps(chapter.bookmarks),
                    dumps(chapter.quotes),
                    dumps(chapter.edit_chain),
                ),
            )
        return chapter

    def get_chapter(self, chapter_id: str) -> ConversationChapter:
        row = self._required_row("SELECT * FROM conversation_chapters WHERE chapter_id = ?", (chapter_id,))
        return chapter_from_row(row)

    def list_chapters(self, *, channel_uuid: str | None = None) -> list[ConversationChapter]:
        rows = self.connection.execute("SELECT * FROM conversation_chapters ORDER BY title, chapter_id").fetchall()
        chapters = [chapter_from_row(row) for row in rows]
        if channel_uuid is None:
            return chapters
        block_rows = self.connection.execute(
            "SELECT block_id FROM conversation_blocks WHERE channel_uuid = ?",
            (channel_uuid,),
        ).fetchall()
        channel_block_ids = {row["block_id"] for row in block_rows}
        return [
            chapter
            for chapter in chapters
            if any(block_id in channel_block_ids for block_id in chapter.member_block_ids)
        ]

    def create_bookmark(
        self,
        *,
        target_type: str,
        target_id: str,
        label: str = "",
        note: str = "",
        scope: str = "both",
        persistence: str = "durable_channel",
        promotion_state: str = "mirrored",
        bookmark_id: str | None = None,
    ) -> ConversationBookmark:
        self._require_bookmark_target(target_type, target_id)
        bookmark = ConversationBookmark(
            bookmark_id=bookmark_id or str(uuid4()),
            target_type=target_type,
            target_id=target_id,
            scope=scope,
            label=label,
            note=note,
            persistence=persistence,
            promotion_state=promotion_state,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_bookmarks(
                    bookmark_id, target_type, target_id, scope, label, note, persistence, promotion_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bookmark.bookmark_id,
                    bookmark.target_type,
                    bookmark.target_id,
                    bookmark.scope,
                    bookmark.label,
                    bookmark.note,
                    bookmark.persistence,
                    bookmark.promotion_state,
                ),
            )
        return bookmark

    def get_bookmark(self, bookmark_id: str) -> ConversationBookmark:
        row = self._required_row("SELECT * FROM conversation_bookmarks WHERE bookmark_id = ?", (bookmark_id,))
        return bookmark_from_row(row)

    def list_bookmarks(
        self,
        *,
        channel_uuid: str | None = None,
        target_type: str | None = None,
    ) -> list[ConversationBookmark]:
        if target_type is not None and target_type not in BOOKMARK_TARGET_TYPES:
            raise ValueError(f"unsupported bookmark target_type: {target_type}")
        params: list[Any] = []
        query = "SELECT * FROM conversation_bookmarks"
        if target_type is not None:
            query += " WHERE target_type = ?"
            params.append(target_type)
        query += " ORDER BY target_type, target_id, bookmark_id"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        bookmarks = [bookmark_from_row(row) for row in rows]
        if channel_uuid is None:
            return bookmarks
        target_ids = self._bookmark_target_ids_for_channel(channel_uuid)
        return [
            bookmark
            for bookmark in bookmarks
            if bookmark.target_id in target_ids.get(bookmark.target_type, set())
        ]

    def create_sticky(
        self,
        *,
        target_type: str,
        target_id: str,
        visibility: str = "visible",
        retention: str = "session",
        clear_state: str = "active",
        sticky_id: str | None = None,
    ) -> ConversationSticky:
        self._require_sticky_target(target_type, target_id)
        sticky = ConversationSticky(
            sticky_id=sticky_id or str(uuid4()),
            target_type=target_type,
            target_id=target_id,
            visibility=visibility,
            retention=retention,
            clear_state=clear_state,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_stickies(sticky_id, target_type, target_id, visibility, retention, clear_state)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sticky.sticky_id, sticky.target_type, sticky.target_id, sticky.visibility, sticky.retention, sticky.clear_state),
            )
        return sticky

    def list_stickies(
        self,
        *,
        channel_uuid: str | None = None,
        target_type: str | None = None,
        clear_state: str | None = None,
    ) -> list[ConversationSticky]:
        if target_type is not None and target_type not in STICKY_TARGET_TYPES:
            raise ValueError(f"unsupported sticky target_type: {target_type}")
        params: list[Any] = []
        query = "SELECT * FROM conversation_stickies"
        clauses: list[str] = []
        if target_type is not None:
            clauses.append("target_type = ?")
            params.append(target_type)
        if clear_state is not None:
            clauses.append("clear_state = ?")
            params.append(clear_state)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY target_type, target_id, sticky_id"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        stickies = [sticky_from_row(row) for row in rows]
        if channel_uuid is None:
            return stickies
        target_ids = self._target_ids_for_channel(channel_uuid)
        return [
            sticky
            for sticky in stickies
            if sticky.target_id in target_ids.get(sticky.target_type, set())
        ]

    def clear_sticky(self, sticky_id: str, *, clear_state: str = "dismissed") -> ConversationSticky:
        with self.connection:
            self.connection.execute(
                "UPDATE conversation_stickies SET clear_state = ? WHERE sticky_id = ?",
                (clear_state, sticky_id),
            )
        return self.get_sticky(sticky_id)

    def get_sticky(self, sticky_id: str) -> ConversationSticky:
        row = self._required_row("SELECT * FROM conversation_stickies WHERE sticky_id = ?", (sticky_id,))
        return sticky_from_row(row)

    def create_quote(
        self,
        *,
        source_type: str,
        source_id: str,
        excerpt: str,
        provenance: JsonDict,
        display_mode: str = "inline",
        quote_id: str | None = None,
    ) -> ConversationQuote:
        self._require_quote_source(source_type, source_id)
        quote = ConversationQuote(
            quote_id=quote_id or str(uuid4()),
            source_type=source_type,
            source_id=source_id,
            excerpt=excerpt,
            provenance=provenance,
            display_mode=display_mode,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_quotes(quote_id, source_type, source_id, excerpt, provenance_json, display_mode)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (quote.quote_id, quote.source_type, quote.source_id, quote.excerpt, dumps(quote.provenance), quote.display_mode),
            )
        return quote

    def list_quotes(
        self,
        *,
        channel_uuid: str | None = None,
        source_type: str | None = None,
    ) -> list[ConversationQuote]:
        if source_type is not None and source_type not in QUOTE_SOURCE_TYPES:
            raise ValueError(f"unsupported quote source_type: {source_type}")
        params: list[Any] = []
        query = "SELECT * FROM conversation_quotes"
        if source_type is not None:
            query += " WHERE source_type = ?"
            params.append(source_type)
        query += " ORDER BY source_type, source_id, quote_id"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        quotes = [quote_from_row(row) for row in rows]
        if channel_uuid is None:
            return quotes
        source_ids = self._target_ids_for_channel(channel_uuid)
        return [
            quote
            for quote in quotes
            if quote.source_id in source_ids.get(quote.source_type, set())
        ]

    def get_quote(self, quote_id: str) -> ConversationQuote:
        row = self._required_row("SELECT * FROM conversation_quotes WHERE quote_id = ?", (quote_id,))
        return quote_from_row(row)

    def apply_edit(
        self,
        *,
        input_ref: JsonDict,
        output_ref: JsonDict,
        edit_type: str,
        reason: str,
        author: str,
        diff_content: JsonDict,
        source_ref: JsonDict | None = None,
        diff_format: str = "jsondiff",
        diff_uri: str | None = None,
        edit_id: str | None = None,
        diff_id: str | None = None,
    ) -> tuple[ConversationEdit, ConversationEditDiff]:
        resolved_edit_id = edit_id or str(uuid4())
        resolved_diff_id = diff_id or str(uuid4())
        diff = ConversationEditDiff(
            diff_id=resolved_diff_id,
            edit_id=resolved_edit_id,
            source_ref=source_ref or input_ref,
            diff_format=diff_format,
            diff_uri=diff_uri or f"sqlite://conversation_edit_diffs/{resolved_diff_id}",
            content=diff_content,
        )
        edit = ConversationEdit(
            edit_id=resolved_edit_id,
            input_ref=input_ref,
            output_ref=output_ref,
            edit_type=edit_type,
            reason=reason,
            author=author,
            diff_id=resolved_diff_id,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_edits(edit_id, input_ref_json, output_ref_json, edit_type, reason, author, diff_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edit.edit_id,
                    dumps(edit.input_ref),
                    dumps(edit.output_ref),
                    edit.edit_type,
                    edit.reason,
                    edit.author,
                    edit.diff_id,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO conversation_edit_diffs(diff_id, edit_id, source_ref_json, diff_format, diff_uri, content_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    diff.diff_id,
                    diff.edit_id,
                    dumps(diff.source_ref),
                    diff.diff_format,
                    diff.diff_uri,
                    dumps(diff.content),
                ),
            )
            chapter_id = chapter_id_from_ref(input_ref) or chapter_id_from_ref(output_ref)
            if chapter_id is not None:
                self._append_chapter_edit(chapter_id, edit.edit_id)
        return edit, diff

    def get_edit(self, edit_id: str) -> ConversationEdit:
        row = self._required_row("SELECT * FROM conversation_edits WHERE edit_id = ?", (edit_id,))
        return edit_from_row(row)

    def list_edits(self, *, edit_type: str | None = None) -> list[ConversationEdit]:
        params: list[Any] = []
        query = "SELECT * FROM conversation_edits"
        if edit_type is not None:
            query += " WHERE edit_type = ?"
            params.append(edit_type)
        query += " ORDER BY edit_type, edit_id"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [edit_from_row(row) for row in rows]

    def get_edit_diff(self, diff_id: str) -> ConversationEditDiff:
        row = self._required_row("SELECT * FROM conversation_edit_diffs WHERE diff_id = ?", (diff_id,))
        return edit_diff_from_row(row)

    def list_edit_diffs(self, *, edit_id: str | None = None) -> list[ConversationEditDiff]:
        params: list[Any] = []
        query = "SELECT * FROM conversation_edit_diffs"
        if edit_id is not None:
            query += " WHERE edit_id = ?"
            params.append(edit_id)
        query += " ORDER BY edit_id, diff_id"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [edit_diff_from_row(row) for row in rows]

    def add_toc_entry(
        self,
        *,
        toc_id: str,
        ordinal: int,
        entry_type: str,
        target_id: str,
        title: str,
        source_scope: JsonDict | None = None,
    ) -> JsonDict:
        entry = {
            "toc_id": toc_id,
            "ordinal": ordinal,
            "entry_type": entry_type,
            "target_id": target_id,
            "title": title,
            "source_scope": source_scope or {},
        }
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conversation_toc_entries(toc_id, ordinal, entry_type, target_id, title, source_scope_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (toc_id, ordinal, entry_type, target_id, title, dumps(entry["source_scope"])),
            )
        return entry

    def list_toc_entries(self, toc_id: str) -> list[JsonDict]:
        rows = self.connection.execute(
            "SELECT * FROM conversation_toc_entries WHERE toc_id = ? ORDER BY ordinal",
            (toc_id,),
        ).fetchall()
        return [
            {
                "toc_id": row["toc_id"],
                "ordinal": row["ordinal"],
                "entry_type": row["entry_type"],
                "target_id": row["target_id"],
                "title": row["title"],
                "source_scope": loads(row["source_scope_json"], {}),
            }
            for row in rows
        ]

    def search(self, term: str, *, channel_uuid: str | None = None) -> list[SearchHit]:
        normalized = normalize_search_term(term)
        if not normalized:
            return []
        params: list[Any] = [normalized]
        channel_filter = ""
        if channel_uuid is not None:
            channel_filter = "AND blocks.channel_uuid = ?"
            params.append(channel_uuid)
        rows = self.connection.execute(
            f"""
            SELECT
                search_terms.term,
                search_terms.block_id,
                search_terms.token_offset,
                search_terms.chapter_id,
                search_terms.cut_id
            FROM conversation_search_terms AS search_terms
            JOIN conversation_blocks AS blocks ON blocks.block_id = search_terms.block_id
            WHERE search_terms.term = ?
            {channel_filter}
            ORDER BY search_terms.block_id, search_terms.token_offset
            """,
            tuple(params),
        ).fetchall()
        return [
            SearchHit(
                term=row["term"],
                block_id=row["block_id"],
                offset=row["token_offset"],
                chapter_id=row["chapter_id"],
                cut_id=row["cut_id"],
            )
            for row in rows
        ]

    def channel_uuid_for_target(self, target_type: str, target_id: str) -> str:
        if target_type == "block":
            return self.get_block(target_id).channel_uuid
        if target_type == "cut":
            return self.channel_uuid_for_target("block", self.get_cut(target_id).source_block_id)
        if target_type == "chapter":
            chapter = self.get_chapter(target_id)
            if not chapter.member_block_ids:
                raise KeyError(target_id)
            return self.channel_uuid_for_target("block", chapter.member_block_ids[0])
        if target_type == "bookmark":
            bookmark = self.get_bookmark(target_id)
            return self.channel_uuid_for_target(bookmark.target_type, bookmark.target_id)
        if target_type == "sticky":
            sticky = self.get_sticky(target_id)
            return self.channel_uuid_for_target(sticky.target_type, sticky.target_id)
        raise ValueError(f"unsupported directive target_type: {target_type}")

    def channel_uuid_for_ref(self, ref: JsonDict) -> str:
        target_type = str(ref.get("type") or "")
        target_id = str(ref.get("id") or "")
        if not target_type or not target_id:
            raise KeyError(ref)
        return self.channel_uuid_for_target(target_type, target_id)

    def count(self, table: str) -> int:
        if table not in TABLE_NAMES:
            raise ValueError(f"unsupported table: {table}")
        row = self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])

    def _index_block(self, block: ConversationBlock) -> None:
        self.connection.execute("DELETE FROM conversation_search_terms WHERE block_id = ?", (block.block_id,))
        rows = [
            (term, block.block_id, offset, None, None)
            for offset, term in enumerate(tokenize_terms(block.presentation))
        ]
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO conversation_search_terms(term, block_id, token_offset, chapter_id, cut_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _required_row(self, query: str, params: tuple[Any, ...]) -> sqlite3.Row:
        row = self.connection.execute(query, params).fetchone()
        if row is None:
            raise KeyError(params[0])
        return row

    def _append_chapter_edit(self, chapter_id: str, edit_id: str) -> None:
        row = self._required_row(
            "SELECT edit_chain_json FROM conversation_chapters WHERE chapter_id = ?",
            (chapter_id,),
        )
        edit_chain = list(loads(row["edit_chain_json"], []))
        if edit_id in edit_chain:
            return
        edit_chain.append(edit_id)
        self.connection.execute(
            "UPDATE conversation_chapters SET edit_chain_json = ? WHERE chapter_id = ?",
            (dumps(edit_chain), chapter_id),
        )

    def _require_bookmark_target(self, target_type: str, target_id: str) -> None:
        self._require_refinement_target(target_type, target_id, "bookmark target_type")

    def _require_quote_source(self, source_type: str, source_id: str) -> None:
        self._require_refinement_target(source_type, source_id, "quote source_type")

    def _require_sticky_target(self, target_type: str, target_id: str) -> None:
        if target_type == "bookmark":
            self.get_bookmark(target_id)
            return
        self._require_refinement_target(target_type, target_id, "sticky target_type")

    def _require_refinement_target(self, target_type: str, target_id: str, name: str) -> None:
        if target_type == "block":
            self.get_block(target_id)
        elif target_type == "cut":
            self.get_cut(target_id)
        elif target_type == "chapter":
            self.get_chapter(target_id)
        else:
            raise ValueError(f"unsupported {name}: {target_type}")

    def _bookmark_target_ids_for_channel(self, channel_uuid: str) -> dict[str, set[str]]:
        return self._target_ids_for_channel(channel_uuid)

    def _target_ids_for_channel(self, channel_uuid: str) -> dict[str, set[str]]:
        block_rows = self.connection.execute(
            "SELECT block_id FROM conversation_blocks WHERE channel_uuid = ?",
            (channel_uuid,),
        ).fetchall()
        block_ids = {row["block_id"] for row in block_rows}
        if not block_ids:
            return {"block": set(), "cut": set(), "chapter": set()}
        cut_rows = self.connection.execute(
            """
            SELECT conversation_cuts.cut_id
            FROM conversation_cuts
            JOIN conversation_blocks ON conversation_blocks.block_id = conversation_cuts.source_block_id
            WHERE conversation_blocks.channel_uuid = ?
            """,
            (channel_uuid,),
        ).fetchall()
        cut_ids = {row["cut_id"] for row in cut_rows}
        chapter_ids = {
            chapter.chapter_id
            for chapter in self.list_chapters(channel_uuid=channel_uuid)
        }
        bookmark_rows = self.connection.execute(
            "SELECT bookmark_id, target_type, target_id FROM conversation_bookmarks"
        ).fetchall()
        source_ids = {"block": block_ids, "cut": cut_ids, "chapter": chapter_ids}
        bookmark_ids = {
            row["bookmark_id"]
            for row in bookmark_rows
            if row["target_id"] in source_ids.get(row["target_type"], set())
        }
        return {"block": block_ids, "cut": cut_ids, "chapter": chapter_ids, "bookmark": bookmark_ids}


def import_replay_rows(
    store: ConversationRefinementStore,
    replay_rows: Iterable[JsonDict],
    *,
    include_payload_errors: bool = False,
) -> list[ConversationBlock]:
    blocks = []
    for row in replay_rows:
        block = conversation_block_from_replay_row(row, include_payload_errors=include_payload_errors)
        if block is not None:
            blocks.append(store.upsert_block(block))
    return blocks


def conversation_block_from_replay_row(row: JsonDict, *, include_payload_errors: bool = False) -> ConversationBlock | None:
    event = dict(row.get("event") or {})
    payload = dict(row.get("payload") or {})
    event_type = str(payload.get("event_type") or event.get("event_type") or "message")
    status = str(payload.get("status") or "")
    if not include_payload_errors and (event_type == "payload_error" or status == "payload_error"):
        return None
    message_uuid = str(payload.get("message_uuid") or event.get("message_uuid") or "")
    if not message_uuid:
        return None
    channel_uuid = str(payload.get("channel_uuid") or event.get("channel_uuid") or "")
    return ConversationBlock(
        block_id=f"message:{message_uuid}",
        channel_uuid=channel_uuid,
        message_uuid=message_uuid,
        sender=str(payload.get("sender") or event.get("nick") or ""),
        sender_type=str(payload.get("sender_type") or event.get("sender_type") or "service"),
        created_at=str(payload.get("created_at") or event.get("timestamp") or ""),
        payload_kind=str(payload.get("payload_kind") or "system"),
        presentation=payload_presentation_text(payload),
        raw_refs=(
            {"kind": "history_event", "message_uuid": message_uuid},
            {"kind": "payload", "message_uuid": message_uuid},
        ),
        metadata={
            "event_type": event_type,
            "history_event": event,
            "frame_count": len(payload.get("frames") or ()),
        },
    )


def run_refinement_storage_proof(root: str | Path) -> JsonDict:
    proof_root = Path(root)
    proof_root.mkdir(parents=True, exist_ok=True)
    db_path = proof_root / "refinement.sqlite3"
    channel_uuid = uuid4().hex
    first_block = ConversationBlock(
        block_id=str(uuid4()),
        channel_uuid=channel_uuid,
        message_uuid=str(uuid4()),
        sender="user",
        sender_type="user",
        created_at=utc_timestamp(),
        payload_kind="text",
        presentation="AlienHand should keep raw conversation searchable.",
        raw_refs=({"kind": "payload", "message_uuid": str(uuid4())},),
    )
    second_block = ConversationBlock(
        block_id=str(uuid4()),
        channel_uuid=channel_uuid,
        message_uuid=str(uuid4()),
        sender="agent",
        sender_type="ai_agent",
        created_at=utc_timestamp(),
        payload_kind="text",
        presentation="The cuts pane assembles refined chapters.",
        raw_refs=({"kind": "payload", "message_uuid": str(uuid4())},),
    )
    with ConversationRefinementStore(db_path) as store:
        store.add_block(first_block)
        store.add_block(second_block)
        cut = store.create_cut(first_block.block_id)
        directive = store.record_directive(
            channel_uuid=first_block.channel_uuid,
            directive_kind="create_cut",
            target_type="cut",
            target_id=cut.cut_id,
            payload={"source_block_id": first_block.block_id},
            result_ref={"type": "cut", "id": cut.cut_id},
        )
        bookmark = store.create_bookmark(
            target_type="block",
            target_id=first_block.block_id,
            label="Searchable source",
            note="Bookmark note echoes into refined views.",
        )
        sticky = store.create_sticky(target_type="bookmark", target_id=bookmark.bookmark_id)
        quote = store.create_quote(
            source_type="block",
            source_id=first_block.block_id,
            excerpt="raw conversation searchable",
            provenance={"block_id": first_block.block_id, "sender": first_block.sender},
        )
        chapter = store.create_chapter(title="Searchable Raw Context", summary="First refinement storage proof.", cut_ids=(cut.cut_id,))
        edit, diff = store.apply_edit(
            input_ref={"type": "chapter", "id": chapter.chapter_id},
            output_ref={"type": "chapter", "id": chapter.chapter_id, "revision": 1},
            edit_type="annotate",
            reason="Record first editorial diff artifact.",
            author="alienhand",
            diff_content={"add": [{"path": "/summary", "value": "First refinement storage proof."}]},
        )
        store.add_toc_entry(
            toc_id="main",
            ordinal=0,
            entry_type="chapter",
            target_id=chapter.chapter_id,
            title=chapter.title,
            source_scope={"block_ids": [first_block.block_id], "cut_ids": [cut.cut_id]},
        )
        hits = store.search("AlienHand")
        toc_entries = store.list_toc_entries("main")
        directives = store.list_directives(channel_uuid=first_block.channel_uuid)
        removed_cut = store.remove_cut(cut.cut_id)
        result = {
            "root": str(proof_root.resolve()),
            "database": str(db_path.resolve()),
            "schema_version": store.schema_version(),
            "blocks": store.count("conversation_blocks"),
            "cuts": store.count("conversation_cuts"),
            "chapters": store.count("conversation_chapters"),
            "bookmarks": store.count("conversation_bookmarks"),
            "stickies": store.count("conversation_stickies"),
            "quotes": store.count("conversation_quotes"),
            "edits": store.count("conversation_edits"),
            "diffs": store.count("conversation_edit_diffs"),
            "directives": store.count("conversation_directives"),
            "search_hits": len(hits),
            "toc_entries": len(toc_entries),
            "directive_kind": directive.directive_kind,
            "directive_sequence": directive.sequence,
            "listed_directives": len(directives),
            "bookmark_note": bookmark.note,
            "sticky_state": sticky.clear_state,
            "quote_excerpt": quote.excerpt,
            "edit_diff_uri": diff.diff_uri,
            "removed_cut_status": removed_cut.status,
            "storage_proof_ok": all(
                [
                    store.schema_version() == SCHEMA_VERSION,
                    store.count("conversation_blocks") == 2,
                    store.count("conversation_directives") == 1,
                    len(hits) == 1,
                    len(toc_entries) == 1,
                    len(directives) == 1,
                    directive.sequence == 1,
                    bookmark.promotion_state == "mirrored",
                    diff.edit_id == edit.edit_id,
                    removed_cut.status == "removed",
                ]
            ),
        }
    return result


def run_refinement_replay_import_proof(root: str | Path, *, app_id: int = 1) -> JsonDict:
    from .chat_platform import (
        ChannelJSONLHistory,
        EnvelopeOutbox,
        PayloadResolver,
        PayloadStore,
        commit_message,
        replay_channel,
    )

    proof_root = Path(root)
    chat_root = proof_root / "chat"
    db_path = proof_root / "refinement.sqlite3"
    channel_uuid = uuid4().hex
    payload_store = PayloadStore(chat_root)
    history = ChannelJSONLHistory(chat_root)
    outbox = EnvelopeOutbox(chat_root / "irc_outbox.jsonl")
    user_message = commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="user",
        sender_type="user",
        payload_kind="text",
        content={"text": "Please preserve this conversation in searchable blocks."},
        store=payload_store,
        history=history,
        publisher=outbox,
    )
    agent_message = commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="agent",
        sender_type="ai_agent",
        payload_kind="mixed",
        content={"text": "Replay import can seed the cuts workbench."},
        frames=({"kind": "code", "language": "python", "code": "print('refine')"},),
        store=payload_store,
        history=history,
        publisher=outbox,
    )
    replay_rows = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    with ConversationRefinementStore(db_path) as refinement_store:
        blocks = import_replay_rows(refinement_store, replay_rows)
        cut = refinement_store.create_cut(blocks[0].block_id)
        chapter = refinement_store.create_chapter(
            title="Replay Imported Context",
            summary="Real chat history became refinement blocks.",
            cut_ids=(cut.cut_id,),
        )
        hits = refinement_store.search("searchable")
        result = {
            "root": str(proof_root.resolve()),
            "database": str(db_path.resolve()),
            "channel_uuid": channel_uuid,
            "imported_blocks": len(blocks),
            "block_ids": [block.block_id for block in blocks],
            "message_uuids": [user_message.envelope.message_uuid, agent_message.envelope.message_uuid],
            "cuts": refinement_store.count("conversation_cuts"),
            "chapters": refinement_store.count("conversation_chapters"),
            "search_hits": len(hits),
            "chapter_id": chapter.chapter_id,
            "replay_import_ok": len(blocks) == 2
            and refinement_store.count("conversation_blocks") == 2
            and len(hits) == 1
            and chapter.member_block_ids == (blocks[0].block_id,),
        }
    return result


def tokenize_terms(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]


def payload_presentation_text(payload: JsonDict) -> str:
    content = payload.get("content")
    parts: list[str] = []
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif content:
            parts.append(dumps(content))
    elif isinstance(content, str):
        parts.append(content)
    frames = payload.get("frames") or ()
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        if isinstance(frame.get("code"), str):
            parts.append(frame["code"])
        elif isinstance(frame.get("text"), str):
            parts.append(frame["text"])
        elif isinstance(frame.get("alt"), str):
            parts.append(frame["alt"])
        elif isinstance(frame.get("title"), str):
            parts.append(frame["title"])
    presentation = "\n".join(part for part in parts if part).strip()
    if presentation:
        return presentation
    event_type = str(payload.get("event_type") or "message")
    message_uuid = str(payload.get("message_uuid") or "")
    return f"[{event_type}] {message_uuid}".strip()


def normalize_search_term(term: str) -> str:
    tokens = tokenize_terms(term)
    return tokens[0] if tokens else ""


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")[:19] + "Z"


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def loads(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


def block_from_row(row: sqlite3.Row) -> ConversationBlock:
    return ConversationBlock(
        block_id=row["block_id"],
        channel_uuid=row["channel_uuid"],
        message_uuid=row["message_uuid"],
        sender=row["sender"],
        sender_type=row["sender_type"],
        created_at=row["created_at"],
        payload_kind=row["payload_kind"],
        presentation=row["presentation"],
        raw_refs=tuple(loads(row["raw_refs_json"], [])),
        metadata=loads(row["metadata_json"], {}),
    )


def cut_from_row(row: sqlite3.Row) -> ConversationCut:
    return ConversationCut(
        cut_id=row["cut_id"],
        source_block_id=row["source_block_id"],
        position=row["position"],
        status=row["status"],
        annotations=tuple(loads(row["annotations_json"], [])),
    )


def chapter_from_row(row: sqlite3.Row) -> ConversationChapter:
    return ConversationChapter(
        chapter_id=row["chapter_id"],
        title=row["title"],
        summary=row["summary"],
        member_cut_ids=tuple(loads(row["member_cut_ids_json"], [])),
        member_block_ids=tuple(loads(row["member_block_ids_json"], [])),
        bookmarks=tuple(loads(row["bookmarks_json"], [])),
        quotes=tuple(loads(row["quotes_json"], [])),
        edit_chain=tuple(loads(row["edit_chain_json"], [])),
    )


def bookmark_from_row(row: sqlite3.Row) -> ConversationBookmark:
    return ConversationBookmark(
        bookmark_id=row["bookmark_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        scope=row["scope"],
        label=row["label"],
        note=row["note"],
        persistence=row["persistence"],
        promotion_state=row["promotion_state"],
    )


def quote_from_row(row: sqlite3.Row) -> ConversationQuote:
    return ConversationQuote(
        quote_id=row["quote_id"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        excerpt=row["excerpt"],
        provenance=loads(row["provenance_json"], {}),
        display_mode=row["display_mode"],
    )


def sticky_from_row(row: sqlite3.Row) -> ConversationSticky:
    return ConversationSticky(
        sticky_id=row["sticky_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        visibility=row["visibility"],
        retention=row["retention"],
        clear_state=row["clear_state"],
    )


def edit_from_row(row: sqlite3.Row) -> ConversationEdit:
    return ConversationEdit(
        edit_id=row["edit_id"],
        input_ref=loads(row["input_ref_json"], {}),
        output_ref=loads(row["output_ref_json"], {}),
        edit_type=row["edit_type"],
        reason=row["reason"],
        author=row["author"],
        diff_id=row["diff_id"],
    )


def edit_diff_from_row(row: sqlite3.Row) -> ConversationEditDiff:
    return ConversationEditDiff(
        diff_id=row["diff_id"],
        edit_id=row["edit_id"],
        source_ref=loads(row["source_ref_json"], {}),
        diff_format=row["diff_format"],
        diff_uri=row["diff_uri"],
        content=loads(row["content_json"], {}),
    )


def directive_from_row(row: sqlite3.Row) -> ConversationDirective:
    return ConversationDirective(
        sequence=row["sequence"],
        directive_id=row["directive_id"],
        channel_uuid=row["channel_uuid"],
        directive_kind=row["directive_kind"],
        source=row["source"],
        visibility=row["visibility"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        payload=loads(row["payload_json"], {}),
        result_ref=loads(row["result_ref_json"], {}),
        created_at=row["created_at"],
    )


def chapter_id_from_ref(ref: JsonDict) -> str | None:
    if ref.get("type") != "chapter":
        return None
    chapter_id = ref.get("id")
    if not isinstance(chapter_id, str) or not chapter_id:
        return None
    return chapter_id


TABLE_NAMES = {
    "conversation_blocks",
    "conversation_cuts",
    "conversation_chapters",
    "conversation_bookmarks",
    "conversation_stickies",
    "conversation_quotes",
    "conversation_edits",
    "conversation_edit_diffs",
    "conversation_toc_entries",
    "conversation_search_terms",
    "conversation_directives",
}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_blocks (
    block_id TEXT PRIMARY KEY,
    channel_uuid TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    sender TEXT NOT NULL,
    sender_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_kind TEXT NOT NULL,
    presentation TEXT NOT NULL,
    raw_refs_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversation_cuts (
    cut_id TEXT PRIMARY KEY,
    source_block_id TEXT NOT NULL REFERENCES conversation_blocks(block_id),
    position INTEGER NOT NULL,
    status TEXT NOT NULL,
    annotations_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS conversation_chapters (
    chapter_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    member_cut_ids_json TEXT NOT NULL DEFAULT '[]',
    member_block_ids_json TEXT NOT NULL DEFAULT '[]',
    bookmarks_json TEXT NOT NULL DEFAULT '[]',
    quotes_json TEXT NOT NULL DEFAULT '[]',
    edit_chain_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS conversation_bookmarks (
    bookmark_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    persistence TEXT NOT NULL,
    promotion_state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_stickies (
    sticky_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    retention TEXT NOT NULL,
    clear_state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_quotes (
    quote_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    display_mode TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_edits (
    edit_id TEXT PRIMARY KEY,
    input_ref_json TEXT NOT NULL,
    output_ref_json TEXT NOT NULL,
    edit_type TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL,
    diff_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_edit_diffs (
    diff_id TEXT PRIMARY KEY,
    edit_id TEXT NOT NULL REFERENCES conversation_edits(edit_id),
    source_ref_json TEXT NOT NULL,
    diff_format TEXT NOT NULL,
    diff_uri TEXT NOT NULL,
    content_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_toc_entries (
    toc_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_scope_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (toc_id, ordinal)
);

CREATE TABLE IF NOT EXISTS conversation_search_terms (
    term TEXT NOT NULL,
    block_id TEXT NOT NULL REFERENCES conversation_blocks(block_id),
    token_offset INTEGER NOT NULL,
    chapter_id TEXT,
    cut_id TEXT,
    PRIMARY KEY (term, block_id, token_offset)
);

CREATE TABLE IF NOT EXISTS conversation_directives (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    directive_id TEXT NOT NULL UNIQUE,
    channel_uuid TEXT NOT NULL,
    directive_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    visibility TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_ref_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_blocks_channel ON conversation_blocks(channel_uuid);
CREATE INDEX IF NOT EXISTS idx_conversation_cuts_position ON conversation_cuts(position);
CREATE INDEX IF NOT EXISTS idx_conversation_bookmarks_target ON conversation_bookmarks(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_conversation_search_terms_term ON conversation_search_terms(term);
CREATE INDEX IF NOT EXISTS idx_conversation_directives_channel_sequence
ON conversation_directives(channel_uuid, sequence);
CREATE INDEX IF NOT EXISTS idx_conversation_directives_kind_sequence
ON conversation_directives(directive_kind, sequence);
"""
