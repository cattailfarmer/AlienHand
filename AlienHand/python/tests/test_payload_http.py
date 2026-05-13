from pathlib import Path
import json
import sys
import tempfile
import unittest
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.chat_platform import (
    AlienHandChatService,
    ChannelJSONLHistory,
    EnvelopeOutbox,
    PayloadStore,
    commit_message,
)
from alienhand_ai.payload_http import PayloadResolverHTTPServer, run_payload_resolver_http_proof


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

            with PayloadResolverHTTPServer(root) as server:
                row, headers, status = fetch_json(server.render_url(published.envelope.message_uuid))

            self.assertEqual(status, 200)
            self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
            self.assertEqual(row["message_uuid"], published.envelope.message_uuid)
            self.assertEqual(row["status"], "resolved")
            self.assertEqual(row["orientation"], "right")
            self.assertEqual(row["frames"][0]["kind"], "code")

    def test_http_resolver_returns_payload_error_for_missing_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            with PayloadResolverHTTPServer(Path(temp)) as server:
                row, _, status = fetch_json(server.render_url(str(uuid4())))

            self.assertEqual(status, 200)
            self.assertEqual(row["status"], "payload_error")
            self.assertEqual(row["content"]["reason"], "payload_not_found")

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

    def test_payload_resolver_http_proof_fetches_resolved_and_missing_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_payload_resolver_http_proof(Path(temp) / "resolver")

            self.assertTrue(result["payload_resolver_fetch_ok"])
            self.assertEqual(result["resolved_status"], "resolved")
            self.assertEqual(result["resolved_orientation"], "right")
            self.assertEqual(result["frame_kinds"], ["code", "link"])
            self.assertEqual(result["missing_status"], "payload_error")
            self.assertTrue(result["cors_ok"])

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
                row, _, status = fetch_json(service.payload_http_server.render_url(str(uuid4())))
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


def fetch_json(url: str):
    request = Request(url, headers={"Accept": "application/json", "Origin": "http://localhost"})
    with urlopen(request, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8")), dict(response.headers), response.status


if __name__ == "__main__":
    unittest.main()
