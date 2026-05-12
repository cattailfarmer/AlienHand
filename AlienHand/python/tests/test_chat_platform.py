from pathlib import Path
import json
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.chat_platform import (
    ChannelJSONLHistory,
    EnvelopeOutbox,
    IRCMessageEnvelope,
    PayloadResolver,
    PayloadStore,
    commit_message,
    replay_channel,
    run_chat_truth_test,
)


class ChatPlatformTests(unittest.TestCase):
    def test_envelope_round_trips_under_budget(self):
        envelope = IRCMessageEnvelope(
            app_id=42,
            channel_uuid=str(uuid4()),
            message_uuid=str(uuid4()),
            nick="agent",
            timestamp="20260512T121314.159Z",
        )

        line = envelope.to_line()
        parsed = IRCMessageEnvelope.from_line(line)

        self.assertLess(len(line.encode("utf-8")), 400)
        self.assertEqual(parsed, envelope)
        self.assertTrue(line.startswith("AH1 "))

    def test_commit_writes_payload_history_then_publishes_envelope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = PayloadStore(root)
            history = ChannelJSONLHistory(root)
            outbox = EnvelopeOutbox(root / "irc_outbox.jsonl")
            channel_uuid = str(uuid4())

            published = commit_message(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="user",
                sender_type="user",
                payload_kind="text",
                content={"text": "hi"},
                store=store,
                history=history,
                publisher=outbox,
                timestamp="20260512T121314.159Z",
            )

            payload_path = store.path_for(published.envelope.message_uuid)
            events = history.load(channel_uuid)
            outbox_lines = (root / "irc_outbox.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertTrue(payload_path.exists())
            self.assertEqual(events[0]["message_uuid"], published.envelope.message_uuid)
            self.assertEqual(outbox.lines, [published.envelope.to_line()])
            self.assertEqual(outbox_lines, [published.envelope.to_line()])
            self.assertEqual(json.loads(payload_path.read_text(encoding="utf-8"))["content"]["text"], "hi")

    def test_history_failure_blocks_envelope_publication(self):
        class FailingHistory(ChannelJSONLHistory):
            def append(self, channel_uuid, event):
                raise RuntimeError("history failed")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = PayloadStore(root)
            outbox = EnvelopeOutbox(root / "irc_outbox.jsonl")

            with self.assertRaises(RuntimeError):
                commit_message(
                    app_id=7,
                    channel_uuid=str(uuid4()),
                    nick="user",
                    sender_type="user",
                    payload_kind="text",
                    content={"text": "hi"},
                    store=store,
                    history=FailingHistory(root),
                    publisher=outbox,
                )

            self.assertEqual(outbox.lines, [])
            self.assertFalse((root / "irc_outbox.jsonl").exists())
            self.assertEqual(len(list((root / "payloads").glob("*.json"))), 1)

    def test_cold_replay_resolves_payloads_and_reports_missing_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = PayloadStore(root)
            history = ChannelJSONLHistory(root)
            outbox = EnvelopeOutbox()
            channel_uuid = str(uuid4())
            published = commit_message(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="agent",
                sender_type="ai_agent",
                payload_kind="text",
                content={"text": "ready"},
                store=store,
                history=history,
                publisher=outbox,
            )
            missing_uuid = str(uuid4())
            history.append(
                channel_uuid,
                {
                    "app_id": 7,
                    "channel_uuid": channel_uuid,
                    "event_type": "message",
                    "message_uuid": missing_uuid,
                    "nick": "service",
                    "sender_type": "service",
                    "timestamp": "20260512T121314.159Z",
                },
            )

            replayed = replay_channel(ChannelJSONLHistory(root), PayloadResolver(PayloadStore(root)), channel_uuid)

            self.assertEqual(replayed[0]["payload"]["message_uuid"], published.envelope.message_uuid)
            self.assertEqual(replayed[0]["payload"]["content"]["text"], "ready")
            self.assertEqual(replayed[1]["payload"]["event_type"], "payload_error")
            self.assertEqual(replayed[1]["payload"]["message_uuid"], missing_uuid)

    def test_chat_truth_test_runs_cold_replay_slice(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_chat_truth_test(Path(temp) / "truth")

            self.assertTrue(result["cold_replay_ok"])
            self.assertEqual(result["envelopes_published"], 2)
            self.assertEqual(result["history_events"], 3)
            self.assertEqual(result["resolved_payloads"], 2)
            self.assertEqual(result["payload_errors"], 1)


if __name__ == "__main__":
    unittest.main()
