from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.refinement_storage import (
    SCHEMA_VERSION,
    ConversationBlock,
    ConversationRefinementStore,
    import_replay_rows,
    run_refinement_replay_import_proof,
    run_refinement_storage_proof,
    tokenize_terms,
    utc_timestamp,
)
from alienhand_ai.chat_platform import (
    ChannelJSONLHistory,
    EnvelopeOutbox,
    PayloadResolver,
    PayloadStore,
    commit_message,
    replay_channel,
)


class RefinementStorageTests(unittest.TestCase):
    def test_store_migrates_to_current_schema_version(self):
        with tempfile.TemporaryDirectory() as temp:
            with ConversationRefinementStore(Path(temp) / "refinement.sqlite3") as store:
                self.assertEqual(store.schema_version(), SCHEMA_VERSION)

    def test_block_persistence_indexes_search_terms(self):
        with tempfile.TemporaryDirectory() as temp:
            block = sample_block("AlienHand keeps raw chat searchable")
            with ConversationRefinementStore(Path(temp) / "refinement.sqlite3") as store:
                store.add_block(block)
                loaded = store.get_block(block.block_id)
                hits = store.search("alienhand")

                self.assertEqual(loaded.presentation, block.presentation)
                self.assertEqual(loaded.raw_refs[0]["message_uuid"], block.message_uuid)
                self.assertEqual([hit.block_id for hit in hits], [block.block_id])
                self.assertIn("searchable", tokenize_terms(block.presentation))

    def test_user_control_flow_creates_cut_chapter_and_removes_cut(self):
        with tempfile.TemporaryDirectory() as temp:
            block = sample_block("Move this useful exchange into the cuts pane")
            with ConversationRefinementStore(Path(temp) / "refinement.sqlite3") as store:
                store.add_block(block)
                cut = store.create_cut(block.block_id)
                chapter = store.create_chapter(title="Useful Exchange", summary="A user-selected cut.", cut_ids=(cut.cut_id,))
                removed = store.remove_cut(cut.cut_id)

                self.assertEqual(cut.position, 0)
                self.assertEqual(chapter.member_cut_ids, (cut.cut_id,))
                self.assertEqual(chapter.member_block_ids, (block.block_id,))
                self.assertEqual(removed.status, "removed")

    def test_cut_and_chapter_lists_can_be_filtered_by_channel(self):
        with tempfile.TemporaryDirectory() as temp:
            first = sample_block("first channel cut source")
            second = sample_block("second channel cut source")
            with ConversationRefinementStore(Path(temp) / "refinement.sqlite3") as store:
                store.add_block(first)
                store.add_block(second)
                first_cut = store.create_cut(first.block_id)
                second_cut = store.create_cut(second.block_id)
                first_chapter = store.create_chapter(
                    title="First Channel",
                    summary="Filtered chapter.",
                    cut_ids=(first_cut.cut_id,),
                )
                store.create_chapter(
                    title="Second Channel",
                    summary="Filtered out.",
                    cut_ids=(second_cut.cut_id,),
                )

                self.assertEqual(
                    [cut.cut_id for cut in store.list_cuts(channel_uuid=first.channel_uuid)],
                    [first_cut.cut_id],
                )
                self.assertEqual(
                    [chapter.chapter_id for chapter in store.list_chapters(channel_uuid=first.channel_uuid)],
                    [first_chapter.chapter_id],
                )

    def test_bookmark_notes_can_be_listed_by_channel_and_target_type(self):
        with tempfile.TemporaryDirectory() as temp:
            first = sample_block("first channel bookmark source")
            second = sample_block("second channel bookmark source")
            with ConversationRefinementStore(Path(temp) / "refinement.sqlite3") as store:
                store.add_block(first)
                store.add_block(second)
                first_cut = store.create_cut(first.block_id)
                second_cut = store.create_cut(second.block_id)
                first_chapter = store.create_chapter(
                    title="First Bookmark Chapter",
                    summary="Filtered bookmark chapter.",
                    cut_ids=(first_cut.cut_id,),
                )
                store.create_chapter(
                    title="Second Bookmark Chapter",
                    summary="Filtered out.",
                    cut_ids=(second_cut.cut_id,),
                )
                block_bookmark = store.create_bookmark(
                    target_type="block",
                    target_id=first.block_id,
                    label="Raw anchor",
                    note="This note should echo from the raw block.",
                )
                cut_bookmark = store.create_bookmark(target_type="cut", target_id=first_cut.cut_id)
                chapter_bookmark = store.create_bookmark(target_type="chapter", target_id=first_chapter.chapter_id)
                store.create_bookmark(target_type="block", target_id=second.block_id)

                first_channel_ids = {
                    bookmark.bookmark_id
                    for bookmark in store.list_bookmarks(channel_uuid=first.channel_uuid)
                }
                first_channel_block_bookmarks = store.list_bookmarks(
                    channel_uuid=first.channel_uuid,
                    target_type="block",
                )

                self.assertEqual(
                    first_channel_ids,
                    {block_bookmark.bookmark_id, cut_bookmark.bookmark_id, chapter_bookmark.bookmark_id},
                )
                self.assertEqual([bookmark.bookmark_id for bookmark in first_channel_block_bookmarks], [block_bookmark.bookmark_id])
                self.assertEqual(first_channel_block_bookmarks[0].note, "This note should echo from the raw block.")
                with self.assertRaises(KeyError):
                    store.create_bookmark(target_type="block", target_id="missing-block")
                with self.assertRaises(ValueError):
                    store.list_bookmarks(target_type="unsupported")

    def test_bookmark_note_sticky_quote_edit_and_toc_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            block = sample_block("Bookmark this source and echo the note")
            with ConversationRefinementStore(Path(temp) / "refinement.sqlite3") as store:
                store.add_block(block)
                cut = store.create_cut(block.block_id)
                chapter = store.create_chapter(title="Bookmark Source", summary="Bookmark mirror proof.", cut_ids=(cut.cut_id,))
                bookmark = store.create_bookmark(
                    target_type="block",
                    target_id=block.block_id,
                    label="Important",
                    note="This note follows the source anchor.",
                )
                sticky = store.create_sticky(target_type="bookmark", target_id=bookmark.bookmark_id)
                quote = store.create_quote(
                    source_type="block",
                    source_id=block.block_id,
                    excerpt="echo the note",
                    provenance={"block_id": block.block_id, "sender": block.sender},
                )
                edit, diff = store.apply_edit(
                    input_ref={"type": "chapter", "id": chapter.chapter_id},
                    output_ref={"type": "chapter", "id": chapter.chapter_id, "revision": 1},
                    edit_type="annotate",
                    reason="Add editorial note.",
                    author="tester",
                    diff_content={"add": [{"path": "/note", "value": bookmark.note}]},
                )
                store.add_toc_entry(
                    toc_id="edit-layer",
                    ordinal=0,
                    entry_type="chapter",
                    target_id=chapter.chapter_id,
                    title=chapter.title,
                    source_scope={"block_ids": [block.block_id], "cut_ids": [cut.cut_id]},
                )

                toc = store.list_toc_entries("edit-layer")
                loaded_bookmark = store.get_bookmark(bookmark.bookmark_id)

                self.assertEqual(loaded_bookmark.note, "This note follows the source anchor.")
                self.assertEqual(loaded_bookmark.promotion_state, "mirrored")
                self.assertEqual(sticky.clear_state, "active")
                self.assertEqual(quote.excerpt, "echo the note")
                self.assertEqual(diff.edit_id, edit.edit_id)
                self.assertTrue(diff.diff_uri.startswith("sqlite://conversation_edit_diffs/"))
                self.assertEqual(toc[0]["source_scope"]["block_ids"], [block.block_id])

    def test_refinement_storage_proof_exercises_core_workflow(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_refinement_storage_proof(Path(temp) / "proof")

            self.assertTrue(result["storage_proof_ok"])
            self.assertEqual(result["schema_version"], SCHEMA_VERSION)
            self.assertEqual(result["blocks"], 2)
            self.assertEqual(result["search_hits"], 1)
            self.assertEqual(result["toc_entries"], 1)
            self.assertEqual(result["removed_cut_status"], "removed")

    def test_import_replay_rows_creates_searchable_blocks_from_chat_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            chat_root = root / "chat"
            channel_uuid = uuid4().hex
            payload_store = PayloadStore(chat_root)
            history = ChannelJSONLHistory(chat_root)
            outbox = EnvelopeOutbox()
            first = commit_message(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="user",
                sender_type="user",
                payload_kind="text",
                content={"text": "raw source should become searchable"},
                store=payload_store,
                history=history,
                publisher=outbox,
            )
            commit_message(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="agent",
                sender_type="ai_agent",
                payload_kind="mixed",
                content={"text": "cuts can begin from replay"},
                frames=({"kind": "code", "language": "python", "code": "print('seed')"},),
                store=payload_store,
                history=history,
                publisher=outbox,
            )
            replay_rows = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)

            with ConversationRefinementStore(root / "refinement.sqlite3") as store:
                blocks = import_replay_rows(store, replay_rows)
                second_import = import_replay_rows(store, replay_rows)
                hits = store.search("searchable")

                self.assertEqual(len(blocks), 2)
                self.assertEqual(len(second_import), 2)
                self.assertEqual(store.count("conversation_blocks"), 2)
                self.assertEqual(blocks[0].block_id, f"message:{first.envelope.message_uuid}")
                self.assertEqual(hits[0].block_id, blocks[0].block_id)

    def test_refinement_replay_import_proof_exercises_chat_to_storage_bridge(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_refinement_replay_import_proof(Path(temp) / "proof")

            self.assertTrue(result["replay_import_ok"])
            self.assertEqual(result["imported_blocks"], 2)
            self.assertEqual(result["cuts"], 1)
            self.assertEqual(result["chapters"], 1)
            self.assertEqual(result["search_hits"], 1)


def sample_block(text: str) -> ConversationBlock:
    message_uuid = str(uuid4())
    return ConversationBlock(
        block_id=str(uuid4()),
        channel_uuid=uuid4().hex,
        message_uuid=message_uuid,
        sender="user",
        sender_type="user",
        created_at=utc_timestamp(),
        payload_kind="text",
        presentation=text,
        raw_refs=({"kind": "payload", "message_uuid": message_uuid},),
    )


if __name__ == "__main__":
    unittest.main()
