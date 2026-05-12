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
    PayloadResolver,
    PayloadStore,
    commit_message,
    irc_channel_name,
    make_local_ergo_config,
    normalize_channel_uuid,
    replay_channel,
    run_chat_truth_test,
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
