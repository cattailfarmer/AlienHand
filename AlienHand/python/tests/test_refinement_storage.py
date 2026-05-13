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
    run_refinement_storage_proof,
    tokenize_terms,
    utc_timestamp,
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
