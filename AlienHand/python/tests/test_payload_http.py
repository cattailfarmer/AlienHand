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
            replayed = replay_channel(history, PayloadResolver(payload_store), channel_uuid)
            with ConversationRefinementStore(root / "refinement.sqlite3") as store:
                blocks = import_replay_rows(store, replayed)

            with PayloadResolverHTTPServer(root, access_token="secret") as server:
                listed, _, listed_status = fetch_json(
                    f"{server.base_url}/alienhand/refinement/blocks?channel={channel_uuid}",
                    token="secret",
                )
                search, _, search_status = fetch_json(
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

            self.assertEqual(listed_status, 200)
            self.assertEqual(listed["blocks"][0]["block_id"], f"message:{published.envelope.message_uuid}")
            self.assertEqual(search_status, 200)
            self.assertEqual([hit["block_id"] for hit in search["hits"]], [blocks[0].block_id])
            self.assertEqual(unauthorized_status, 401)
            self.assertEqual(unauthorized["error"], "unauthorized")
            self.assertEqual(cut_status, 201)
            self.assertEqual(cut["cut"]["source_block_id"], blocks[0].block_id)
            self.assertEqual(chapter_status, 201)
            self.assertEqual(chapter["chapter"]["member_cut_ids"], [cut["cut"]["cut_id"]])
            self.assertEqual(removed_status, 200)
            self.assertEqual(removed["cut"]["status"], "removed")
            self.assertEqual(cuts_status, 200)
            self.assertEqual(len(cuts["cuts"]), 1)
            self.assertEqual(cuts["cuts"][0]["status"], "removed")
            self.assertEqual(active_cuts_status, 200)
            self.assertEqual(active_cuts["cuts"], [])
            self.assertEqual(chapters_status, 200)
            self.assertEqual(len(chapters["chapters"]), 1)

    def test_refinement_http_api_proof_exercises_workbench_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_refinement_http_api_proof(Path(temp) / "refinement-http")

            self.assertTrue(result["refinement_http_ok"])
            self.assertEqual(result["imported_blocks"], 1)
            self.assertEqual(result["listed_blocks"], 1)
            self.assertEqual(result["search_hits"], 1)
            self.assertEqual(result["listed_cuts"], 1)
            self.assertEqual(result["listed_chapters"], 1)
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

    def test_chat_service_reports_missing_thelounge_build_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AlienHandChatService(
                Path(temp) / "service",
                thelounge_root=Path(temp) / "missing-build",
                start_thelounge=True,
            )

            with self.assertRaisesRegex(RuntimeError, "The Lounge build is missing"):
                service._require_thelounge_build()


def fetch_json(url: str, *, token: str | None = None):
    headers = {"Accept": "application/json", "Origin": "http://localhost"}
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
