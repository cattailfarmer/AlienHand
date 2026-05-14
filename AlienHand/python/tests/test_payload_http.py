from pathlib import Path
import json
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.chat_platform import (
    AlienHandChatService,
    ChannelJSONLHistory,
    EnvelopeOutbox,
    PayloadResolver,
    PayloadStore,
    commit_message,
    replay_channel,
)
from alienhand_ai.payload_http import (
    PayloadResolverHTTPServer,
    run_payload_resolver_http_proof,
    run_refinement_http_api_proof,
)
from alienhand_ai.refinement_storage import ConversationRefinementStore, import_replay_rows


class PayloadHTTPTests(unittest.TestCase):
    def test_http_resolver_serves_render_model_for_payload_uuid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            channel_uuid = uuid4().hex
            published = commit_message(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="agent",
                sender_type="ai_agent",
                payload_kind="code",
                content={"text": "code frame"},
                frames=({"kind": "code", "language": "python", "code": "print('ok')"},),
                store=PayloadStore(root),
                history=ChannelJSONLHistory(root),
                publisher=EnvelopeOutbox(),
            )

            with PayloadResolverHTTPServer(root, access_token="secret") as server:
                row, headers, status = fetch_json(server.render_url(published.envelope.message_uuid), token="secret")

            self.assertEqual(status, 200)
            self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
            self.assertEqual(row["message_uuid"], published.envelope.message_uuid)
            self.assertEqual(row["status"], "resolved")
            self.assertEqual(row["orientation"], "right")
            self.assertEqual(row["frames"][0]["kind"], "code")

    def test_http_resolver_returns_payload_error_for_missing_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            with PayloadResolverHTTPServer(Path(temp), access_token="secret") as server:
                row, _, status = fetch_json(server.render_url(str(uuid4())), token="secret")

            self.assertEqual(status, 200)
            self.assertEqual(row["status"], "payload_error")
            self.assertEqual(row["content"]["reason"], "payload_not_found")

    def test_http_resolver_rejects_unauthorized_payload_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            with PayloadResolverHTTPServer(Path(temp), access_token="secret") as server:
                row, headers, status = fetch_json(server.render_url(str(uuid4())))

            self.assertEqual(status, 401)
            self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
            self.assertEqual(row["error"], "unauthorized")

    def test_http_resolver_supports_browser_options_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            with PayloadResolverHTTPServer(Path(temp)) as server:
                request = Request(
                    server.render_url(str(uuid4())),
                    method="OPTIONS",
                    headers={"Origin": "http://localhost"},
                )
                with urlopen(request, timeout=5.0) as response:
                    response.read()
                    status = response.status
                    headers = dict(response.headers)

            self.assertEqual(status, 204)
            self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
            self.assertIn("GET", headers["Access-Control-Allow-Methods"])
            self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])

    def test_http_resolver_restricts_cors_to_allowed_origins(self):
        with tempfile.TemporaryDirectory() as temp:
            with PayloadResolverHTTPServer(
                Path(temp),
                access_token="secret",
                allowed_origins=("http://127.0.0.1:19000",),
            ) as server:
                _, allowed_headers, allowed_status = fetch_json(
                    server.render_url(str(uuid4())),
                    token="secret",
                    origin="http://127.0.0.1:19000",
                )
                _, blocked_headers, blocked_status = fetch_json(
                    server.render_url(str(uuid4())),
                    token="secret",
                    origin="http://example.invalid",
                )

            self.assertEqual(allowed_status, 200)
            self.assertEqual(allowed_headers["Access-Control-Allow-Origin"], "http://127.0.0.1:19000")
            self.assertEqual(allowed_headers["Vary"], "Origin")
            self.assertEqual(blocked_status, 200)
            self.assertNotIn("Access-Control-Allow-Origin", blocked_headers)

    def test_payload_resolver_http_proof_fetches_resolved_and_missing_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_payload_resolver_http_proof(Path(temp) / "resolver")

            self.assertTrue(result["payload_resolver_fetch_ok"])
            self.assertEqual(result["resolved_status"], "resolved")
            self.assertEqual(result["resolved_orientation"], "right")
            self.assertEqual(result["frame_kinds"], ["code", "link"])
            self.assertEqual(result["missing_status"], "payload_error")
            self.assertEqual(result["unauthorized_status"], 401)
            self.assertTrue(result["cors_ok"])

    def test_refinement_http_api_lists_searches_and_creates_user_controlled_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            channel_uuid = uuid4().hex
            other_channel_uuid = uuid4().hex
            payload_store = PayloadStore(root)
            history = ChannelJSONLHistory(root)
            published = commit_message(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="user",
                sender_type="user",
                payload_kind="text",
                content={"text": "searchable refinement API block"},
                store=payload_store,
                history=history,
                publisher=EnvelopeOutbox(),
            )
            commit_message(
                app_id=7,
                channel_uuid=other_channel_uuid,
                nick="other-user",
                sender_type="user",
                payload_kind="text",
                content={"text": "searchable refinement API block from another channel"},
                store=payload_store,
                history=history,
                publisher=EnvelopeOutbox(),
            )
            replayed = replay_channel(history, PayloadResolver(payload_store), channel_uuid)
            other_replayed = replay_channel(history, PayloadResolver(payload_store), other_channel_uuid)
            with ConversationRefinementStore(root / "refinement.sqlite3") as store:
                blocks = import_replay_rows(store, replayed)
                other_blocks = import_replay_rows(store, other_replayed)

            with PayloadResolverHTTPServer(root, access_token="secret") as server:
                listed, _, listed_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/blocks?channel={channel_uuid}",
                    token="secret",
                )
                search, _, search_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/search?q=api&channel={channel_uuid}",
                    token="secret",
                )
                unscoped_search, _, unscoped_search_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/search?q=api",
                    token="secret",
                )
                unauthorized, _, unauthorized_status = fetch_json(f"{server.base_url}/alienhand/refinement/blocks")
                cut, _, cut_status = post_json(
                    f"{server.base_url}/alienhand/refinement/cuts",
                    {"source_block_id": blocks[0].block_id},
                    token="secret",
                )
                chapter, _, chapter_status = post_json(
                    f"{server.base_url}/alienhand/refinement/chapters",
                    {
                        "title": "Refinement API",
                        "summary": "User-selected cut from raw conversation.",
                        "cut_ids": [cut["cut"]["cut_id"]],
                    },
                    token="secret",
                )
                edit, _, edit_status = post_json(
                    f"{server.base_url}/alienhand/refinement/edits",
                    {
                        "input_ref": {"type": "chapter", "id": chapter["chapter"]["chapter_id"]},
                        "output_ref": {"type": "chapter", "id": chapter["chapter"]["chapter_id"], "revision": 1},
                        "edit_type": "annotate",
                        "reason": "Expose editorial edit through HTTP.",
                        "author": "tester",
                        "diff_content": {"add": [{"path": "/summary", "value": "HTTP edit proof"}]},
                    },
                    token="secret",
                )
                missing_edit, _, missing_edit_status = post_json(
                    f"{server.base_url}/alienhand/refinement/edits",
                    {
                        "input_ref": {"type": "chapter", "id": "missing-chapter"},
                        "output_ref": {"type": "chapter", "id": "missing-chapter", "revision": 1},
                        "edit_type": "annotate",
                        "reason": "Missing chapter should not commit.",
                        "author": "tester",
                        "diff_content": {"add": []},
                    },
                    token="secret",
                )
                toc_entry, _, toc_entry_status = post_json(
                    f"{server.base_url}/alienhand/refinement/toc",
                    {
                        "toc_id": "edit-layer",
                        "ordinal": 0,
                        "entry_type": "chapter",
                        "target_id": chapter["chapter"]["chapter_id"],
                        "title": "Refinement API",
                        "source_scope": {"block_ids": [blocks[0].block_id], "cut_ids": [cut["cut"]["cut_id"]]},
                    },
                    token="secret",
                )
                bookmark, _, bookmark_status = post_json(
                    f"{server.base_url}/alienhand/refinement/bookmarks",
                    {
                        "target_type": "block",
                        "target_id": blocks[0].block_id,
                        "label": "Remember",
                        "note": "Bookmark notes stay rooted in the raw source.",
                    },
                    token="secret",
                )
                missing_bookmark, _, missing_bookmark_status = post_json(
                    f"{server.base_url}/alienhand/refinement/bookmarks",
                    {"target_type": "block", "target_id": "missing-block"},
                    token="secret",
                )
                quote, _, quote_status = post_json(
                    f"{server.base_url}/alienhand/refinement/quotes",
                    {
                        "source_type": "block",
                        "source_id": blocks[0].block_id,
                        "excerpt": "searchable refinement API",
                        "provenance": {"block_id": blocks[0].block_id},
                    },
                    token="secret",
                )
                missing_quote, _, missing_quote_status = post_json(
                    f"{server.base_url}/alienhand/refinement/quotes",
                    {"source_type": "block", "source_id": "missing-block", "excerpt": "missing"},
                    token="secret",
                )
                sticky, _, sticky_status = post_json(
                    f"{server.base_url}/alienhand/refinement/stickies",
                    {
                        "target_type": "bookmark",
                        "target_id": bookmark["bookmark"]["bookmark_id"],
                    },
                    token="secret",
                )
                missing_sticky, _, missing_sticky_status = post_json(
                    f"{server.base_url}/alienhand/refinement/stickies",
                    {"target_type": "bookmark", "target_id": "missing-bookmark"},
                    token="secret",
                )
                removed, _, removed_status = delete_json(
                    f"{server.base_url}/alienhand/refinement/cuts/{cut['cut']['cut_id']}",
                    token="secret",
                )
                cuts, _, cuts_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/cuts?channel={channel_uuid}",
                    token="secret",
                )
                active_cuts, _, active_cuts_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/cuts?channel={channel_uuid}&status=active",
                    token="secret",
                )
                chapters, _, chapters_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/chapters?channel={channel_uuid}",
                    token="secret",
                )
                edits, _, edits_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/edits?edit_type=annotate",
                    token="secret",
                )
                edit_diffs, _, edit_diffs_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/edit-diffs?edit_id={edit['edit']['edit_id']}",
                    token="secret",
                )
                edit_diff, _, edit_diff_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/edit-diffs/{edit['diff']['diff_id']}",
                    token="secret",
                )
                toc_entries, _, toc_entries_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/toc?toc_id=edit-layer",
                    token="secret",
                )
                bookmarks, _, bookmarks_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/bookmarks?channel={channel_uuid}",
                    token="secret",
                )
                block_bookmarks, _, block_bookmarks_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/bookmarks?channel={channel_uuid}&target_type=block",
                    token="secret",
                )
                quotes, _, quotes_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/quotes?channel={channel_uuid}",
                    token="secret",
                )
                block_quotes, _, block_quotes_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/quotes?channel={channel_uuid}&source_type=block",
                    token="secret",
                )
                stickies, _, stickies_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/stickies?channel={channel_uuid}&clear_state=active",
                    token="secret",
                )
                bookmark_stickies, _, bookmark_stickies_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/stickies?channel={channel_uuid}&target_type=bookmark&clear_state=active",
                    token="secret",
                )
                cleared_sticky, _, cleared_sticky_status = delete_json(
                    f"{server.base_url}/alienhand/refinement/stickies/{sticky['sticky']['sticky_id']}",
                    token="secret",
                )
                active_stickies_after_clear, _, active_stickies_after_clear_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/stickies?channel={channel_uuid}&clear_state=active",
                    token="secret",
                )
                dismissed_stickies, _, dismissed_stickies_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/stickies?channel={channel_uuid}&clear_state=dismissed",
                    token="secret",
                )
                directives, _, directives_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/directives?channel={channel_uuid}",
                    token="secret",
                )
                directives_after_first, _, directives_after_first_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/directives?channel={channel_uuid}&after_sequence=1",
                    token="secret",
                )
                limited_directives, _, limited_directives_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/directives?channel={channel_uuid}&limit=3",
                    token="secret",
                )
                cut_directives, _, cut_directives_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/directives?channel={channel_uuid}&directive_kind=create_cut",
                    token="secret",
                )

            self.assertEqual(listed_status, 200)
            self.assertEqual(listed["blocks"][0]["block_id"], f"message:{published.envelope.message_uuid}")
            self.assertEqual(search_status, 200)
            self.assertEqual([hit["block_id"] for hit in search["hits"]], [blocks[0].block_id])
            self.assertEqual(unscoped_search_status, 200)
            self.assertEqual(
                {hit["block_id"] for hit in unscoped_search["hits"]},
                {blocks[0].block_id, other_blocks[0].block_id},
            )
            self.assertEqual(unauthorized_status, 401)
            self.assertEqual(unauthorized["error"], "unauthorized")
            self.assertEqual(cut_status, 201)
            self.assertEqual(cut["cut"]["source_block_id"], blocks[0].block_id)
            self.assertEqual(chapter_status, 201)
            self.assertEqual(chapter["chapter"]["member_cut_ids"], [cut["cut"]["cut_id"]])
            self.assertEqual(edit_status, 201)
            self.assertEqual(edit["edit"]["input_ref"]["id"], chapter["chapter"]["chapter_id"])
            self.assertEqual(edit["diff"]["edit_id"], edit["edit"]["edit_id"])
            self.assertEqual(missing_edit_status, 404)
            self.assertEqual(missing_edit["error"], "edit_target_not_found")
            self.assertEqual(toc_entry_status, 201)
            self.assertEqual(toc_entry["entry"]["target_id"], chapter["chapter"]["chapter_id"])
            self.assertEqual(bookmark_status, 201)
            self.assertEqual(bookmark["bookmark"]["target_id"], blocks[0].block_id)
            self.assertEqual(bookmark["bookmark"]["note"], "Bookmark notes stay rooted in the raw source.")
            self.assertEqual(missing_bookmark_status, 404)
            self.assertEqual(missing_bookmark["error"], "bookmark_target_not_found")
            self.assertEqual(quote_status, 201)
            self.assertEqual(quote["quote"]["source_id"], blocks[0].block_id)
            self.assertEqual(quote["quote"]["excerpt"], "searchable refinement API")
            self.assertEqual(missing_quote_status, 404)
            self.assertEqual(missing_quote["error"], "quote_source_not_found")
            self.assertEqual(sticky_status, 201)
            self.assertEqual(sticky["sticky"]["target_type"], "bookmark")
            self.assertEqual(sticky["sticky"]["target_id"], bookmark["bookmark"]["bookmark_id"])
            self.assertEqual(missing_sticky_status, 404)
            self.assertEqual(missing_sticky["error"], "sticky_target_not_found")
            self.assertEqual(removed_status, 200)
            self.assertEqual(removed["cut"]["status"], "removed")
            self.assertEqual(cuts_status, 200)
            self.assertEqual(len(cuts["cuts"]), 1)
            self.assertEqual(cuts["cuts"][0]["status"], "removed")
            self.assertEqual(active_cuts_status, 200)
            self.assertEqual(active_cuts["cuts"], [])
            self.assertEqual(chapters_status, 200)
            self.assertEqual(len(chapters["chapters"]), 1)
            self.assertEqual(chapters["chapters"][0]["edit_chain"], [edit["edit"]["edit_id"]])
            self.assertEqual(edits_status, 200)
            self.assertEqual([row["edit_id"] for row in edits["edits"]], [edit["edit"]["edit_id"]])
            self.assertEqual(edit_diffs_status, 200)
            self.assertEqual([row["diff_id"] for row in edit_diffs["diffs"]], [edit["diff"]["diff_id"]])
            self.assertEqual(edit_diff_status, 200)
            self.assertEqual(edit_diff["diff"]["content"]["add"][0]["value"], "HTTP edit proof")
            self.assertEqual(toc_entries_status, 200)
            self.assertEqual([row["target_id"] for row in toc_entries["entries"]], [chapter["chapter"]["chapter_id"]])
            self.assertEqual(bookmarks_status, 200)
            self.assertEqual([row["bookmark_id"] for row in bookmarks["bookmarks"]], [bookmark["bookmark"]["bookmark_id"]])
            self.assertEqual(block_bookmarks_status, 200)
            self.assertEqual([row["bookmark_id"] for row in block_bookmarks["bookmarks"]], [bookmark["bookmark"]["bookmark_id"]])
            self.assertEqual(quotes_status, 200)
            self.assertEqual([row["quote_id"] for row in quotes["quotes"]], [quote["quote"]["quote_id"]])
            self.assertEqual(block_quotes_status, 200)
            self.assertEqual([row["quote_id"] for row in block_quotes["quotes"]], [quote["quote"]["quote_id"]])
            self.assertEqual(stickies_status, 200)
            self.assertEqual([row["sticky_id"] for row in stickies["stickies"]], [sticky["sticky"]["sticky_id"]])
            self.assertEqual(bookmark_stickies_status, 200)
            self.assertEqual(
                [row["sticky_id"] for row in bookmark_stickies["stickies"]],
                [sticky["sticky"]["sticky_id"]],
            )
            self.assertEqual(cleared_sticky_status, 200)
            self.assertEqual(cleared_sticky["sticky"]["clear_state"], "dismissed")
            self.assertEqual(active_stickies_after_clear_status, 200)
            self.assertEqual(active_stickies_after_clear["stickies"], [])
            self.assertEqual(dismissed_stickies_status, 200)
            self.assertEqual(
                [row["sticky_id"] for row in dismissed_stickies["stickies"]],
                [sticky["sticky"]["sticky_id"]],
            )
            self.assertEqual(directives_status, 200)
            self.assertEqual([row["sequence"] for row in directives["directives"]], list(range(1, 10)))
            self.assertEqual(
                [row["directive_kind"] for row in directives["directives"]],
                [
                    "create_cut",
                    "create_chapter",
                    "apply_edit",
                    "add_toc_entry",
                    "create_bookmark",
                    "quote_span",
                    "pin_sticky",
                    "remove_cut",
                    "clear_sticky",
                ],
            )
            self.assertEqual(directives["directives"][0]["result_ref"], {"id": cut["cut"]["cut_id"], "type": "cut"})
            self.assertEqual(directives_after_first_status, 200)
            self.assertEqual([row["sequence"] for row in directives_after_first["directives"]], list(range(2, 10)))
            self.assertEqual(limited_directives_status, 200)
            self.assertEqual([row["sequence"] for row in limited_directives["directives"]], [1, 2, 3])
            self.assertEqual(cut_directives_status, 200)
            self.assertEqual([row["target_id"] for row in cut_directives["directives"]], [cut["cut"]["cut_id"]])

    def test_refinement_http_api_can_adopt_live_irc_message_as_block(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            channel_uuid = uuid4().hex
            with PayloadResolverHTTPServer(root, access_token="secret") as server:
                block, _, block_status = post_json(
                    f"{server.base_url}/alienhand/refinement/blocks",
                    {
                        "block_id": f"thelounge:{channel_uuid}:42",
                        "channel_uuid": channel_uuid,
                        "message_uuid": f"thelounge-{channel_uuid}-42",
                        "sender": "alienhanduser00",
                        "sender_type": "user",
                        "created_at": "2026-05-13T13:57:00.000Z",
                        "payload_kind": "irc_text",
                        "presentation": "ffffffffffff",
                        "raw_refs": [{"kind": "thelounge_message", "message_id": 42}],
                        "metadata": {"source": "thelounge_live_chat"},
                    },
                    token="secret",
                )
                cut, _, cut_status = post_json(
                    f"{server.base_url}/alienhand/refinement/cuts",
                    {"source_block_id": block["block"]["block_id"]},
                    token="secret",
                )
                listed, _, listed_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/blocks?channel={channel_uuid}",
                    token="secret",
                )

            self.assertEqual(block_status, 201)
            self.assertEqual(block["block"]["sender"], "alienhanduser00")
            self.assertEqual(block["block"]["presentation"], "ffffffffffff")
            self.assertEqual(cut_status, 201)
            self.assertEqual(cut["cut"]["source_block_id"], block["block"]["block_id"])
            self.assertEqual(listed_status, 200)
            self.assertEqual([row["block_id"] for row in listed["blocks"]], [block["block"]["block_id"]])

    def test_refinement_http_api_proof_exercises_workbench_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_refinement_http_api_proof(Path(temp) / "refinement-http")

            self.assertTrue(result["refinement_http_ok"])
            self.assertEqual(result["imported_blocks"], 1)
            self.assertEqual(result["listed_blocks"], 1)
            self.assertEqual(result["search_hits"], 1)
            self.assertEqual(result["listed_cuts"], 1)
            self.assertEqual(result["listed_chapters"], 1)
            self.assertEqual(result["listed_edits"], 1)
            self.assertEqual(result["listed_diffs"], 1)
            self.assertEqual(result["listed_toc_entries"], 1)
            self.assertEqual(result["listed_bookmarks"], 1)
            self.assertEqual(result["bookmark_note"], "Bookmark note follows the source block.")
            self.assertEqual(result["listed_quotes"], 1)
            self.assertEqual(result["quote_excerpt"], "searchable workbench source")
            self.assertEqual(result["listed_stickies"], 1)
            self.assertEqual(result["sticky_target_type"], "bookmark")
            self.assertEqual(result["cleared_sticky_state"], "dismissed")
            self.assertEqual(result["active_stickies_after_clear"], 0)
            self.assertEqual(result["listed_directives"], 9)
            self.assertEqual(result["directive_sequences"], list(range(1, 10)))
            self.assertEqual(result["directives_after_first"], 8)
            self.assertEqual(result["limited_directives"], 3)
            self.assertEqual(result["create_cut_directives"], 1)
            self.assertEqual(result["removed_cut_status"], "removed")
            self.assertEqual(result["unauthorized_status"], 401)

    def test_chat_service_owns_payload_resolver_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AlienHandChatService(Path(temp) / "service")
            service._start_payload_http_server()
            try:
                self.assertIsNotNone(service.payload_resolver_base_url)
                self.assertIsNotNone(service.payload_http_server)
                self.assertEqual(
                    service.thelounge_environment()["ALIENHAND_PAYLOAD_RESOLVER"],
                    service.payload_resolver_base_url,
                )
                self.assertTrue(service.thelounge_environment()["ALIENHAND_PAYLOAD_RESOLVER_TOKEN"])
                row, _, status = fetch_json(
                    service.payload_http_server.render_url(str(uuid4())),
                    token=service.payload_resolver_token,
                )
            finally:
                service.stop()

            self.assertEqual(status, 200)
            self.assertEqual(row["status"], "payload_error")
            self.assertIsNone(service.payload_resolver_base_url)
            self.assertIsNone(service.payload_http_server)

    def test_chat_service_requires_payload_resolver_before_thelounge_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AlienHandChatService(Path(temp) / "service")

            with self.assertRaises(RuntimeError):
                service.thelounge_environment()

    def test_chat_service_builds_thelounge_command_for_app_owned_ergo(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AlienHandChatService(Path(temp) / "service", start_thelounge=True)
            service.port = 16667
            service.thelounge_port = 19000

            command = service._thelounge_command()

            self.assertEqual(command[:2], ["node", "index.js"])
            self.assertEqual(command[-1], "start")
            self.assertIn("host=127.0.0.1", command)
            self.assertIn("port=19000", command)
            self.assertIn("public=true", command)
            self.assertIn("lockNetwork=true", command)
            self.assertIn("defaults.name=AlienHand", command)
            self.assertIn("defaults.host=127.0.0.1", command)
            self.assertIn("defaults.port=16667", command)
            self.assertIn("defaults.tls=false", command)
            self.assertIn("defaults.nick=alienhanduser%%", command)

    def test_chat_service_limits_resolver_cors_to_thelounge_origins(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AlienHandChatService(Path(temp) / "service", start_thelounge=True)
            service.thelounge_port = 19000

            self.assertEqual(
                service._thelounge_allowed_origins(),
                ("http://127.0.0.1:19000", "http://localhost:19000"),
            )

    def test_chat_service_reports_missing_thelounge_build_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AlienHandChatService(
                Path(temp) / "service",
                thelounge_root=Path(temp) / "missing-build",
                start_thelounge=True,
            )

            with self.assertRaisesRegex(RuntimeError, "The Lounge build is missing"):
                service._require_thelounge_build()


def fetch_json(url: str, *, token: str | None = None, origin: str = "http://localhost"):
    headers = {"Accept": "application/json", "Origin": origin}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers), response.status
    except HTTPError as error:
        return json.loads(error.read().decode("utf-8")), dict(error.headers), error.code


def post_json(url: str, body: dict[str, object], *, token: str | None = None):
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Origin": "http://localhost"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers), response.status
    except HTTPError as error:
        return json.loads(error.read().decode("utf-8")), dict(error.headers), error.code


def delete_json(url: str, *, token: str | None = None):
    headers = {"Accept": "application/json", "Origin": "http://localhost"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="DELETE")
    try:
        with urlopen(request, timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers), response.status
    except HTTPError as error:
        return json.loads(error.read().decode("utf-8")), dict(error.headers), error.code


if __name__ == "__main__":
    unittest.main()
