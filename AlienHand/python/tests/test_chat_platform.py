from pathlib import Path
import json
import socket
import sys
import tempfile
import threading
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.chat_platform import (
    AlienHandChatService,
    ChannelJSONLHistory,
    EnvelopeOutbox,
    IRCEnvelopeReceiver,
    IRCMessageEnvelope,
    IRCNetworkPublisher,
    PayloadObject,
    PayloadResolver,
    PayloadStore,
    commit_message,
    handle_history_command,
    irc_channel_name,
    make_local_ergo_config,
    normalize_channel_uuid,
    payload_to_render_model,
    parse_history_command,
    record_history_request,
    replay_channel,
    replay_channel_chunks,
    replay_channel_render_models,
    run_chat_truth_test,
    run_history_replay_proof,
    run_render_model_proof,
)


class ChatPlatformTests(unittest.TestCase):
    def test_envelope_round_trips_under_budget(self):
        envelope = IRCMessageEnvelope(
            app_id=42,
            channel_uuid=uuid4().hex,
            message_uuid=str(uuid4()),
            nick="agent",
            timestamp="20260512T121314.159Z",
        )

        line = envelope.to_line()
        parsed = IRCMessageEnvelope.from_line(line)

        self.assertLess(len(line.encode("utf-8")), 400)
        self.assertEqual(parsed, envelope)
        self.assertEqual(len(parsed.channel_uuid), 32)
        self.assertTrue(line.startswith("AH1 "))

    def test_channel_uuid_normalizes_to_plain_hex_channel_name(self):
        channel = uuid4()
        channel_hex = channel.hex

        self.assertEqual(normalize_channel_uuid(str(channel)), channel_hex)
        self.assertEqual(normalize_channel_uuid(f"#{channel_hex}"), channel_hex)
        self.assertEqual(irc_channel_name(str(channel)), f"#{channel_hex}")
        self.assertEqual(len(irc_channel_name(str(channel))), 33)

    def test_commit_writes_payload_history_then_publishes_envelope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = PayloadStore(root)
            history = ChannelJSONLHistory(root)
            outbox = EnvelopeOutbox(root / "irc_outbox.jsonl")
            channel_uuid = uuid4().hex

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
            self.assertEqual(published.payload.channel_uuid, channel_uuid)
            self.assertEqual(published.history_event["channel_uuid"], channel_uuid)
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
                    channel_uuid=uuid4().hex,
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
            channel_uuid = uuid4().hex
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

    def test_chunked_replay_backfills_recent_full_messages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = PayloadStore(root)
            history = ChannelJSONLHistory(root)
            outbox = EnvelopeOutbox()
            channel_uuid = uuid4().hex

            for index in range(1, 6):
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
            record_history_request(
                app_id=7,
                channel_uuid=channel_uuid,
                nick="user",
                requester_type="user",
                request={"mode": "last_messages", "messages": 3, "chunk_size": 2},
                store=store,
                history=history,
                publisher=outbox,
            )

            chunks = replay_channel_chunks(
                ChannelJSONLHistory(root),
                PayloadResolver(PayloadStore(root)),
                channel_uuid,
                chunk_size=2,
                limit=3,
                event_types=("message",),
            )

            self.assertEqual([chunk["event_count"] for chunk in chunks], [2, 1])
            self.assertEqual([row["payload"]["content"]["text"] for row in chunks[0]["events"]], ["message 4", "message 5"])
            self.assertEqual([row["payload"]["content"]["text"] for row in chunks[1]["events"]], ["message 3"])
            self.assertTrue(chunks[0]["has_more"])
            self.assertFalse(chunks[1]["has_more"])

    def test_parse_history_command_supports_count_all_and_chunk_size(self):
        default_request = parse_history_command("!ah history")
        counted_request = parse_history_command("!ah history 12 chunk 4")
        equals_request = parse_history_command("!ah history 9 --chunk=3")
        all_request = parse_history_command("!ah history all chunk-size 7")

        self.assertIsNotNone(default_request)
        self.assertEqual(default_request["mode"], "last_messages")
        self.assertEqual(default_request["messages"], 50)
        self.assertEqual(default_request["chunk_size"], 10)
        self.assertEqual(counted_request["messages"], 12)
        self.assertEqual(counted_request["chunk_size"], 4)
        self.assertEqual(equals_request["messages"], 9)
        self.assertEqual(equals_request["chunk_size"], 3)
        self.assertEqual(all_request["mode"], "all_messages")
        self.assertIsNone(all_request["messages"])
        self.assertEqual(all_request["chunk_size"], 7)
        self.assertIsNone(parse_history_command("hello history"))

        with self.assertRaises(ValueError):
            parse_history_command("!ah history all 5")

        with self.assertRaises(ValueError):
            parse_history_command("!ah history 5 all")

        with self.assertRaises(ValueError):
            parse_history_command("!ah history 5 chunk 0")

    def test_history_command_records_request_and_returns_replay_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = PayloadStore(root)
            history = ChannelJSONLHistory(root)
            outbox = EnvelopeOutbox()
            channel_uuid = uuid4().hex

            for index in range(1, 4):
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

            result = handle_history_command(
                "!ah history 2 chunk 1",
                app_id=7,
                channel_uuid=channel_uuid,
                nick="user",
                requester_type="user",
                store=store,
                history=history,
                resolver=PayloadResolver(store),
                publisher=outbox,
                metadata={"proof": "history_command"},
            )

            self.assertIsNotNone(result)
            self.assertEqual(result["request"]["mode"], "last_messages")
            self.assertEqual(result["request"]["messages"], 2)
            self.assertEqual(result["chunk_lengths"], [1, 1])
            self.assertEqual(result["chunks"][0]["events"][0]["payload"]["content"]["text"], "message 3")
            self.assertEqual(result["chunks"][1]["events"][0]["payload"]["content"]["text"], "message 2")
            self.assertEqual(result["resolved_payloads"], 2)
            self.assertEqual(len(history.load(channel_uuid)), 4)
            self.assertEqual(history.load(channel_uuid)[-1]["event_type"], "history_request")
            self.assertEqual(len(outbox.lines), 4)

    def test_history_replay_proof_records_request_and_chunks_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_history_replay_proof(Path(temp) / "history", message_count=5, chunk_size=2, limit=4)

            self.assertTrue(result["request_recorded"])
            self.assertTrue(result["history_command_ok"])
            self.assertEqual(result["history_command"], "!ah history 4 chunk 2")
            self.assertEqual(result["history_command_request"]["messages"], 4)
            self.assertTrue(result["cold_replay_ok"])
            self.assertEqual(result["history_events"], 6)
            self.assertEqual(result["outbox_envelopes"], 6)
            self.assertEqual(result["chunk_lengths"], [2, 2])
            self.assertEqual(result["first_chunk_texts"], ["message 4", "message 5"])

    def test_payload_render_model_orients_sender_and_normalizes_frames(self):
        payload = PayloadObject(
            message_uuid=str(uuid4()),
            app_id=7,
            channel_uuid=uuid4().hex,
            sender="agent",
            sender_type="ai_agent",
            event_type="message",
            payload_kind="mixed",
            created_at="20260512T121314.159Z",
            content={"text": "frames"},
            frames=({"kind": "code", "language": "python", "code": "print('ok')"},),
        )

        row = payload_to_render_model(payload)

        self.assertEqual(row["orientation"], "right")
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["frames"][0]["kind"], "code")
        self.assertEqual(row["frames"][0]["text"], "print('ok')")

    def test_replay_render_models_surface_payload_errors_as_system_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            history = ChannelJSONLHistory(root)
            channel_uuid = uuid4().hex
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

            rows = replay_channel_render_models(history, PayloadResolver(PayloadStore(root)), channel_uuid)

            self.assertEqual(rows[0]["status"], "payload_error")
            self.assertEqual(rows[0]["orientation"], "system")
            self.assertEqual(rows[0]["message_uuid"], missing_uuid)

    def test_render_model_proof_builds_bubbles_frames_and_payload_error(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_render_model_proof(Path(temp) / "render")

            self.assertTrue(result["render_model_ok"])
            self.assertEqual(result["orientation_sequence"], ["left", "right", "system"])
            self.assertEqual(result["agent_frame_kinds"], ["code", "image"])
            self.assertEqual(result["payload_error_rows"], 1)

    def test_network_publisher_sends_join_and_privmsg(self):
        with tempfile.TemporaryDirectory() as temp, FakeIRCServer() as server:
            root = Path(temp)
            channel_uuid = uuid4().hex
            publisher = IRCNetworkPublisher("127.0.0.1", server.port, "agent", timeout=2.0)
            try:
                published = commit_message(
                    app_id=7,
                    channel_uuid=channel_uuid,
                    nick="agent",
                    sender_type="ai_agent",
                    payload_kind="text",
                    content={"text": "over IRC"},
                    store=PayloadStore(root),
                    history=ChannelJSONLHistory(root),
                    publisher=publisher,
                )
            finally:
                publisher.close()

            commands = server.commands
            self.assertIn("NICK agent", commands)
            self.assertTrue(any(command.startswith("USER alienhand") for command in commands))
            self.assertIn(f"JOIN {irc_channel_name(channel_uuid)}", commands)
            self.assertIn(f"PRIVMSG {irc_channel_name(channel_uuid)} :{published.envelope.to_line()}", commands)
            self.assertTrue(PayloadStore(root).path_for(published.envelope.message_uuid).exists())

    def test_envelope_receiver_waits_for_channel_privmsg(self):
        channel_uuid = uuid4().hex
        envelope = IRCMessageEnvelope(
            app_id=7,
            channel_uuid=channel_uuid,
            message_uuid=str(uuid4()),
            nick="agent",
            timestamp="20260512T121314.159Z",
        )

        with FakeIRCEnvelopeServer(envelope) as server:
            receiver = IRCEnvelopeReceiver("127.0.0.1", server.port, "user", timeout=2.0)
            try:
                target = receiver.join(channel_uuid)
                received = receiver.wait_for_envelope(channel_uuid, timeout=2.0)
            finally:
                receiver.close()

        self.assertEqual(target, irc_channel_name(channel_uuid))
        self.assertEqual(received.envelope, envelope)
        self.assertEqual(received.target, irc_channel_name(channel_uuid))
        self.assertIn("PRIVMSG", received.raw_line)
        self.assertIn(f"JOIN {irc_channel_name(channel_uuid)}", server.commands)

    def test_local_ergo_config_uses_loopback_port_and_runtime_paths(self):
        template = """network:
    name: ErgoTest
server:
    name: ergo.test
    listeners:
        "127.0.0.1:6667":
        "[::1]:6667":
        ":6697":
            tls:
                cert: fullchain.pem
                key: privkey.pem
    unix-bind-mode: 0777
lock-file: "ircd.lock"
datastore:
    path: ircd.db
languages:
    enabled: true
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = make_local_ergo_config(
                template,
                port=16667,
                datastore_path=root / "ircd.db",
                lock_path=root / "ircd.lock",
            )

            self.assertIn('"127.0.0.1:16667":', config)
            self.assertNotIn('":6697":', config)
            self.assertNotIn('"[::1]:6667":', config)
            self.assertIn((root / "ircd.db").resolve().as_posix(), config)
            self.assertIn((root / "ircd.lock").resolve().as_posix(), config)

    def test_app_chat_service_requires_start_before_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AlienHandChatService(Path(temp) / "app-chat")

            with self.assertRaises(RuntimeError):
                service.publish_text("hello before start")

            service.stop()


class FakeIRCServer:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self._ready = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "FakeIRCServer":
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._socket.close()
        self._thread.join(timeout=2.0)

    def _serve(self) -> None:
        self._ready.set()
        try:
            connection, _ = self._socket.accept()
        except OSError:
            return
        nick = "agent"
        with connection:
            connection.settimeout(2.0)
            buffer = b""
            while True:
                try:
                    chunk = connection.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                while b"\r\n" in buffer:
                    raw, buffer = buffer.split(b"\r\n", 1)
                    command = raw.decode("utf-8")
                    self.commands.append(command)
                    if command.startswith("NICK "):
                        nick = command.split(" ", 1)[1]
                    elif command.startswith("USER "):
                        connection.sendall(f":fake 001 {nick} :welcome\r\n".encode("utf-8"))
                    elif command.startswith("QUIT "):
                        return


class FakeIRCEnvelopeServer:
    def __init__(self, envelope: IRCMessageEnvelope) -> None:
        self.envelope = envelope
        self.commands: list[str] = []
        self._ready = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "FakeIRCEnvelopeServer":
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._socket.close()
        self._thread.join(timeout=2.0)

    def _serve(self) -> None:
        self._ready.set()
        try:
            connection, _ = self._socket.accept()
        except OSError:
            return
        nick = "user"
        with connection:
            connection.settimeout(2.0)
            buffer = b""
            while True:
                try:
                    chunk = connection.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                while b"\r\n" in buffer:
                    raw, buffer = buffer.split(b"\r\n", 1)
                    command = raw.decode("utf-8")
                    self.commands.append(command)
                    if command.startswith("NICK "):
                        nick = command.split(" ", 1)[1]
                    elif command.startswith("USER "):
                        connection.sendall(f":fake 001 {nick} :welcome\r\n".encode("utf-8"))
                    elif command.startswith("JOIN "):
                        target = command.split(" ", 1)[1]
                        connection.sendall(f":{nick}!user@localhost JOIN {target}\r\n".encode("utf-8"))
                        connection.sendall(
                            f":agent!agent@localhost PRIVMSG {target} :{self.envelope.to_line()}\r\n".encode("utf-8")
                        )
                    elif command.startswith("QUIT "):
                        return


if __name__ == "__main__":
    unittest.main()
