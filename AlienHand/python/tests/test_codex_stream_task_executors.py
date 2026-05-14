import sys
from pathlib import Path
import json
import unittest
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.chat_platform import ChannelJSONLHistory, EnvelopeOutbox, PayloadStore, commit_message, record_history_request
from alienhand_ai.codex_stream_task_executors import execute_alienhand_task, execute_history_context_pack, execute_stubbed_task


class CodexStreamTaskExecutorsTests(unittest.TestCase):
    def test_health_check_stub_reports_complete(self):
        result = execute_stubbed_task({"task_type": "health_check"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result_summary"], "health check stub executed")

    def test_sop_compile_stub_reports_needs_human(self):
        result = execute_stubbed_task({"task_type": "sop_compile"})
        self.assertEqual(result["status"], "needs_human")
        self.assertIn("SOP compile", result["result_summary"])
        self.assertEqual(result["error_ref"], "stubbed_task:sop_compile")

    def test_unknown_task_stub_requests_human(self):
        result = execute_stubbed_task({"task_type": "mystery_task"})
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["error_ref"], "stubbed_task:unsupported")

    def test_tool_handoff_has_error_reference(self):
        result = execute_stubbed_task({"task_type": "tool_handoff"})
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["error_ref"], "stubbed_task:tool_handoff")

    def test_alienhand_task_without_runtime_root_preserves_stubbed_history_pack(self):
        result = execute_alienhand_task({"task_type": "history_context_pack", "input_ref": "{}"})
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["error_ref"], "stubbed_task:history_context_pack")

    def test_history_context_pack_writes_anchored_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            channel_uuid = uuid4().hex
            store = PayloadStore(root)
            history = ChannelJSONLHistory(root)
            outbox = EnvelopeOutbox()
            for index in range(1, 3):
                commit_message(
                    app_id=7,
                    channel_uuid=channel_uuid,
                    nick="agent",
                    sender_type="ai_agent",
                    payload_kind="text",
                    content={"text": f"message {index}"},
                    store=store,
                    history=history,
                    publisher=outbox,
                )
            request_message = record_history_request(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="alice",
                requester_type="user",
                request={"mode": "last_messages", "messages": 2, "chunk_size": 1},
                store=store,
                history=history,
                publisher=outbox,
            )
            commit_message(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="agent",
                sender_type="ai_agent",
                payload_kind="text",
                content={"text": "message after request"},
                store=store,
                history=history,
                publisher=outbox,
            )
            result = execute_history_context_pack(
                {
                    "request_id": "request-history-pack-a1",
                    "app_id": 7,
                    "channel_uuid": channel_uuid,
                    "requester_kind": "user",
                    "requester_id": "alice",
                    "task_type": "history_context_pack",
                    "input_ref": json.dumps(
                        {
                            "task": "history_context_pack",
                            "request_message_uuid": request_message.envelope.message_uuid,
                            "messages": 2,
                            "chunk_size": 1,
                            "direction": "recent_first_backfill",
                        },
                        sort_keys=True,
                    ),
                    "evidence_refs": [],
                },
                root=root,
            )
            artifact_path = Path(result["artifacts"][0]["metadata"]["filesystem_path"])
            artifact_path_exists = artifact_path.exists()
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "completed")
        self.assertTrue(artifact_path_exists)
        self.assertEqual(result["result_ref"], result["artifacts"][0]["artifact_uri"])
        self.assertEqual(result["artifacts"][0]["artifact_kind"], "history_context_pack")
        self.assertEqual(artifact["summary"]["resolved_payloads"], 2)
        self.assertEqual(artifact["summary"]["payload_errors"], 0)
        self.assertTrue(artifact["anchor"]["found"])
        self.assertEqual([chunk["event_count"] for chunk in artifact["chunks"]], [1, 1])
        self.assertEqual(artifact["chunks"][0]["events"][0]["payload"]["content"]["text"], "message 2")
        self.assertEqual(artifact["chunks"][1]["events"][0]["payload"]["content"]["text"], "message 1")
        self.assertNotIn("message after request", json.dumps(artifact))


if __name__ == "__main__":
    unittest.main()
