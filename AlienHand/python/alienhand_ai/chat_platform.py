from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
from time import time
from typing import Any, Protocol
from uuid import UUID, uuid4

from .refinement_storage import (
    ConversationBlock,
    ConversationRefinementStore,
    conversation_block_from_replay_row,
    import_replay_rows,
)


JsonDict = dict[str, Any]
IRC_BODY_BUDGET_BYTES = 400
RECENT_FIRST_BACKFILL = "recent_first_backfill"
OLDEST_FIRST_RECONSTRUCT = "oldest_first_reconstruct"
AH_HISTORY_COMMAND = "!ah history"
AH_HISTORY_COMMAND_SYNTAX = "!ah history [all|<messages>] [chunk <chunk_size>]"
DEFAULT_HISTORY_COMMAND_MESSAGES = 50
DEFAULT_HISTORY_COMMAND_CHUNK_SIZE = 10
MAX_HISTORY_COMMAND_MESSAGES = 500
MAX_HISTORY_COMMAND_CHUNK_SIZE = 100


class EnvelopePublisher(Protocol):
    def publish(self, envelope: "IRCMessageEnvelope") -> str:
        ...


@dataclass(frozen=True)
class IRCMessageEnvelope:
    app_id: int
    channel_uuid: str
    message_uuid: str
    nick: str
    timestamp: str
    protocol: str = "AH1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel_uuid", normalize_channel_uuid(self.channel_uuid))

    def to_line(self) -> str:
        values = {
            "a": str(self.app_id),
            "c": normalize_channel_uuid(self.channel_uuid),
            "m": self.message_uuid,
            "n": self.nick,
            "t": self.timestamp,
        }
        for key, value in values.items():
            _require_wire_token(key, value)
        line = f"{self.protocol} a={values['a']} c={values['c']} m={values['m']} n={values['n']} t={values['t']}"
        if len(line.encode("utf-8")) > IRC_BODY_BUDGET_BYTES:
            raise ValueError("IRC envelope exceeds the prototype body budget")
        return line

    def to_dict(self) -> JsonDict:
        return asdict(self)

    @classmethod
    def from_line(cls, line: str) -> "IRCMessageEnvelope":
        parts = line.strip().split()
        if not parts or parts[0] != "AH1":
            raise ValueError("unsupported IRC envelope protocol")
        values = _parse_wire_tokens(parts[1:])
        return cls(
            protocol=parts[0],
            app_id=int(values["a"]),
            channel_uuid=values["c"],
            message_uuid=values["m"],
            nick=values["n"],
            timestamp=values["t"],
        )


@dataclass(frozen=True)
class PayloadObject:
    message_uuid: str
    app_id: int
    channel_uuid: str
    sender: str
    sender_type: str
    event_type: str
    payload_kind: str
    created_at: str
    content: JsonDict
    frames: tuple[JsonDict, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: JsonDict) -> "PayloadObject":
        data = dict(payload)
        data["frames"] = tuple(data.get("frames", ()))
        data["metadata"] = data.get("metadata", {})
        return cls(**data)


@dataclass(frozen=True)
class PublishedMessage:
    envelope: IRCMessageEnvelope
    payload: PayloadObject
    history_event: JsonDict


@dataclass(frozen=True)
class ReceivedIRCEnvelope:
    envelope: IRCMessageEnvelope
    target: str
    raw_line: str


class PayloadStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.payloads_dir = self.root / "payloads"

    def path_for(self, message_uuid: str) -> Path:
        return self.payloads_dir / f"{message_uuid}.json"

    def write(self, payload: PayloadObject) -> Path:
        target = self.path_for(payload.message_uuid)
        if target.exists():
            raise FileExistsError(f"payload already exists: {payload.message_uuid}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".json.tmp")
        encoded = json.dumps(payload.to_dict(), indent=2, sort_keys=True)
        try:
            with temp.open("w", encoding="utf-8") as file:
                file.write(encoded)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()
        return target

    def read(self, message_uuid: str) -> PayloadObject:
        path = self.path_for(message_uuid)
        with path.open("r", encoding="utf-8") as file:
            return PayloadObject.from_dict(json.load(file))


class PayloadResolver:
    def __init__(self, store: PayloadStore) -> None:
        self.store = store

    def resolve(self, message_uuid: str) -> PayloadObject:
        return self.store.read(message_uuid)

    def resolve_render_model(self, message_uuid: str) -> JsonDict:
        try:
            return self.resolve(message_uuid).to_dict()
        except FileNotFoundError:
            return payload_error(message_uuid, "payload_not_found")


class ChannelJSONLHistory:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.channels_dir = self.root / "channels"

    def path_for(self, channel_uuid: str) -> Path:
        return self.channels_dir / f"{normalize_channel_uuid(channel_uuid)}.jsonl"

    def append(self, channel_uuid: str, event: JsonDict) -> Path:
        path = self.path_for(channel_uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, sort_keys=True))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        return path

    def load(self, channel_uuid: str) -> list[JsonDict]:
        path = self.path_for(channel_uuid)
        if not path.exists():
            return []
        events = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    events.append(json.loads(line))
        return events


class EnvelopeOutbox:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.lines: list[str] = []

    def publish(self, envelope: IRCMessageEnvelope) -> str:
        line = envelope.to_line()
        self.lines.append(line)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
        return line


class IRCNetworkPublisher:
    def __init__(
        self,
        host: str,
        port: int,
        nick: str,
        *,
        username: str = "alienhand",
        realname: str = "AlienHand",
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.nick = nick
        self.username = username
        self.realname = realname
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._file = None

    def connect(self) -> None:
        if self._socket is not None:
            return
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._socket = sock
        self._file = sock.makefile("r", encoding="utf-8", newline="\r\n")
        self._send_raw(f"NICK {self.nick}")
        self._send_raw(f"USER {self.username} 0 * :{self.realname}")
        self._wait_for_registration()

    def publish(self, envelope: IRCMessageEnvelope) -> str:
        self.connect()
        target = irc_channel_name(envelope.channel_uuid)
        line = envelope.to_line()
        self._send_raw(f"JOIN {target}")
        self._send_raw(f"PRIVMSG {target} :{line}")
        return line

    def close(self) -> None:
        try:
            if self._socket is not None:
                self._send_raw("QUIT :AlienHand shutdown")
        except OSError:
            pass
        if self._file is not None:
            self._file.close()
        if self._socket is not None:
            self._socket.close()
        self._file = None
        self._socket = None

    def __enter__(self) -> "IRCNetworkPublisher":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _wait_for_registration(self) -> None:
        while True:
            line = self._read_raw()
            parts = line.split()
            if len(parts) > 1 and parts[1] == "001":
                return
            if parts and parts[0] == "PING":
                token = parts[1] if len(parts) > 1 else ""
                self._send_raw(f"PONG {token}")

    def _send_raw(self, line: str) -> None:
        if self._socket is None:
            raise RuntimeError("IRC publisher is not connected")
        self._socket.sendall(f"{line}\r\n".encode("utf-8"))

    def _read_raw(self) -> str:
        if self._file is None:
            raise RuntimeError("IRC publisher is not connected")
        line = self._file.readline()
        if not line:
            raise ConnectionError("IRC server closed connection before registration completed")
        return line.rstrip("\r\n")


class IRCEnvelopeReceiver:
    def __init__(
        self,
        host: str,
        port: int,
        nick: str,
        *,
        username: str = "alienhanduser",
        realname: str = "AlienHand User",
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.nick = nick
        self.username = username
        self.realname = realname
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._file = None

    def connect(self) -> None:
        if self._socket is not None:
            return
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._socket = sock
        self._file = sock.makefile("r", encoding="utf-8", newline="\r\n")
        self._send_raw(f"NICK {self.nick}")
        self._send_raw(f"USER {self.username} 0 * :{self.realname}")
        self._wait_for_registration()

    def join(self, channel_uuid: str) -> str:
        self.connect()
        target = irc_channel_name(channel_uuid)
        self._send_raw(f"JOIN {target}")
        self._wait_for_join(target)
        return target

    def wait_for_envelope(self, channel_uuid: str, *, timeout: float | None = None) -> ReceivedIRCEnvelope:
        self.connect()
        target = irc_channel_name(channel_uuid)
        deadline = time() + (timeout if timeout is not None else self.timeout)
        while time() < deadline:
            self._set_socket_timeout(deadline)
            try:
                line = self._read_raw()
            except TimeoutError:
                break
            if self._handle_ping(line):
                continue
            privmsg = _parse_irc_privmsg(line)
            if privmsg is None or privmsg["target"].lower() != target.lower():
                continue
            try:
                envelope = IRCMessageEnvelope.from_line(privmsg["message"])
            except ValueError:
                continue
            if envelope.channel_uuid == normalize_channel_uuid(channel_uuid):
                return ReceivedIRCEnvelope(envelope=envelope, target=privmsg["target"], raw_line=line)
        raise TimeoutError(f"Timed out waiting for AH1 envelope on {target}")

    def close(self) -> None:
        try:
            if self._socket is not None:
                self._send_raw("QUIT :AlienHand user client shutdown")
        except OSError:
            pass
        if self._file is not None:
            self._file.close()
        if self._socket is not None:
            self._socket.close()
        self._file = None
        self._socket = None

    def __enter__(self) -> "IRCEnvelopeReceiver":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _wait_for_registration(self) -> None:
        while True:
            line = self._read_raw()
            parts = line.split()
            if len(parts) > 1 and parts[1] == "001":
                return
            self._handle_ping(line)

    def _wait_for_join(self, target: str) -> None:
        deadline = time() + self.timeout
        while time() < deadline:
            self._set_socket_timeout(deadline)
            try:
                line = self._read_raw()
            except TimeoutError:
                break
            if self._handle_ping(line):
                continue
            if _line_confirms_join(line, target):
                return
        raise TimeoutError(f"Timed out waiting to join {target}")

    def _handle_ping(self, line: str) -> bool:
        parts = line.split()
        if parts and parts[0] == "PING":
            token = parts[1] if len(parts) > 1 else ""
            self._send_raw(f"PONG {token}")
            return True
        return False

    def _set_socket_timeout(self, deadline: float) -> None:
        if self._socket is not None:
            self._socket.settimeout(max(0.1, deadline - time()))

    def _send_raw(self, line: str) -> None:
        if self._socket is None:
            raise RuntimeError("IRC receiver is not connected")
        self._socket.sendall(f"{line}\r\n".encode("utf-8"))

    def _read_raw(self) -> str:
        if self._file is None:
            raise RuntimeError("IRC receiver is not connected")
        try:
            line = self._file.readline()
        except socket.timeout as error:
            raise TimeoutError("timed out waiting for IRC receiver line") from error
        if not line:
            raise ConnectionError("IRC server closed connection")
        return line.rstrip("\r\n")


class AlienHandChatService:
    def __init__(
        self,
        root: str | Path,
        *,
        ergo_root: str | Path | None = None,
        app_id: int = 1,
        nick: str = "alienhandagent",
        port: int | None = None,
        payload_resolver_host: str = "127.0.0.1",
        payload_resolver_port: int | None = None,
        payload_resolver_token: str | None = None,
        start_payload_resolver: bool = True,
        thelounge_root: str | Path | None = None,
        thelounge_host: str = "127.0.0.1",
        thelounge_port: int | None = None,
        start_thelounge: bool = False,
        start_refinement_store: bool = True,
        refinement_db_path: str | Path | None = None,
        startup_timeout: float = 30.0,
        build_timeout: float = 120.0,
    ) -> None:
        self.root = Path(root)
        self.ergo_root = Path(ergo_root) if ergo_root else default_ergo_root()
        self.app_id = app_id
        self.nick = nick
        self.port = port
        self.payload_resolver_host = payload_resolver_host
        self.payload_resolver_port = payload_resolver_port
        self.payload_resolver_token = payload_resolver_token or secrets.token_urlsafe(32)
        self.start_payload_resolver = start_payload_resolver
        self.thelounge_root = Path(thelounge_root) if thelounge_root else default_thelounge_root()
        self.thelounge_host = thelounge_host
        self.thelounge_port = thelounge_port
        self.start_thelounge = start_thelounge
        self.start_refinement_store = start_refinement_store
        self.startup_timeout = startup_timeout
        self.build_timeout = build_timeout
        self.runtime_dir = self.root / "ergo-runtime"
        self.thelounge_runtime_dir = self.root / "thelounge-runtime"
        self.refinement_db_path = Path(refinement_db_path) if refinement_db_path else self.root / "refinement.sqlite3"
        self.store = PayloadStore(self.root)
        self.history = ChannelJSONLHistory(self.root)
        self.resolver = PayloadResolver(self.store)
        self.publisher: EnvelopePublisher | None = None
        self.refinement_store: ConversationRefinementStore | None = None
        self.payload_http_server: Any | None = None
        self.thelounge_process: subprocess.Popen | None = None
        self.process: subprocess.Popen | None = None
        self.config_path: Path | None = None
        self.binary_path: Path | None = None
        self.stdout_path = self.runtime_dir / "ergo.stdout.log"
        self.stderr_path = self.runtime_dir / "ergo.stderr.log"
        self.thelounge_stdout_path = self.thelounge_runtime_dir / "thelounge.stdout.log"
        self.thelounge_stderr_path = self.thelounge_runtime_dir / "thelounge.stderr.log"
        self._stdout_file = None
        self._stderr_file = None
        self._thelounge_stdout_file = None
        self._thelounge_stderr_file = None

    def start(self) -> "AlienHandChatService":
        if self.process is not None and self.process.poll() is None:
            if self.start_payload_resolver and self.payload_http_server is None:
                self._start_payload_http_server()
            if self.start_thelounge and self.thelounge_process is None:
                self._start_thelounge_process()
            return self
        if self.process is not None:
            self.stop()
        resolved_port = self.port or find_free_port()
        self.port = resolved_port
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = write_local_ergo_config(self.ergo_root, self.runtime_dir, resolved_port)
        self.binary_path = build_ergo_binary(self.ergo_root, self.runtime_dir, timeout=self.build_timeout)
        self._stdout_file = self.stdout_path.open("w", encoding="utf-8")
        self._stderr_file = self.stderr_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(self.binary_path), "run", "--conf", str(self.config_path), "--quiet"],
            cwd=self.ergo_root,
            stdout=self._stdout_file,
            stderr=self._stderr_file,
            text=True,
        )
        try:
            wait_for_tcp("127.0.0.1", resolved_port, timeout=self.startup_timeout)
            self.publisher = IRCNetworkPublisher("127.0.0.1", resolved_port, self.nick, timeout=5.0)
            self.publisher.connect()
            if self.start_payload_resolver:
                self._start_payload_http_server()
            if self.start_thelounge:
                self._start_thelounge_process()
        except Exception:
            self.stop()
            raise
        return self

    @property
    def payload_resolver_base_url(self) -> str | None:
        if self.payload_http_server is None:
            return None
        return str(self.payload_http_server.base_url)

    def publish_text(
        self,
        text: str,
        *,
        channel_uuid: str | None = None,
        nick: str | None = None,
        sender_type: str = "ai_agent",
        event_type: str = "message",
        metadata: JsonDict | None = None,
    ) -> PublishedMessage:
        if self.publisher is None:
            raise RuntimeError("AlienHandChatService must be started before publishing")
        published = commit_message(
            app_id=self.app_id,
            channel_uuid=channel_uuid or uuid4().hex,
            nick=nick or self.nick,
            sender_type=sender_type,
            payload_kind="text",
            event_type=event_type,
            content={"text": text},
            metadata=metadata,
            store=self.store,
            history=self.history,
            publisher=self.publisher,
        )
        self.ingest_published_message(published)
        return published

    def replay_channel(self, channel_uuid: str) -> list[JsonDict]:
        return replay_channel(self.history, self.resolver, channel_uuid)

    def ingest_published_message(self, published: PublishedMessage) -> ConversationBlock | None:
        if not self.start_refinement_store:
            return None
        block = conversation_block_from_replay_row(
            {"event": published.history_event, "payload": published.payload.to_dict()}
        )
        if block is None:
            return None
        return self._ensure_refinement_store().upsert_block(block)

    def sync_refinement_channel(self, channel_uuid: str) -> list[ConversationBlock]:
        if not self.start_refinement_store:
            return []
        return import_replay_rows(self._ensure_refinement_store(), self.replay_channel(channel_uuid))

    def thelounge_environment(self) -> dict[str, str]:
        base_url = self.payload_resolver_base_url
        if base_url is None:
            raise RuntimeError("payload resolver must be started before exporting thelounge environment")
        return {
            "ALIENHAND_PAYLOAD_RESOLVER": base_url,
            "ALIENHAND_PAYLOAD_RESOLVER_TOKEN": self.payload_resolver_token,
        }

    @property
    def thelounge_base_url(self) -> str | None:
        if self.thelounge_process is None:
            return None
        return f"http://{self.thelounge_host}:{self.thelounge_port}"

    def stop(self) -> None:
        if self.thelounge_process is not None:
            stop_process(self.thelounge_process, timeout=5.0)
            self.thelounge_process = None
        if self._thelounge_stdout_file is not None:
            self._thelounge_stdout_file.close()
            self._thelounge_stdout_file = None
        if self._thelounge_stderr_file is not None:
            self._thelounge_stderr_file.close()
            self._thelounge_stderr_file = None
        if self.payload_http_server is not None:
            self.payload_http_server.stop()
            self.payload_http_server = None
        if self.publisher is not None and hasattr(self.publisher, "close"):
            self.publisher.close()
            self.publisher = None
        else:
            self.publisher = None
        if self.refinement_store is not None:
            self.refinement_store.close()
            self.refinement_store = None
        if self.process is not None:
            stop_process(self.process, timeout=5.0)
            self.process = None
        if self._stdout_file is not None:
            self._stdout_file.close()
            self._stdout_file = None
        if self._stderr_file is not None:
            self._stderr_file.close()
            self._stderr_file = None

    def __enter__(self) -> "AlienHandChatService":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()

    def _start_payload_http_server(self) -> None:
        if self.payload_http_server is not None:
            return
        from .payload_http import PayloadResolverHTTPServer

        allowed_origins = None
        if self.start_thelounge:
            if self.thelounge_port is None:
                self.thelounge_port = find_free_port()
            allowed_origins = self._thelounge_allowed_origins()
        self.payload_http_server = PayloadResolverHTTPServer(
            self.root,
            host=self.payload_resolver_host,
            port=self.payload_resolver_port,
            access_token=self.payload_resolver_token,
            allowed_origins=allowed_origins,
        ).start()
        self.payload_resolver_port = int(self.payload_http_server.port)

    def _thelounge_allowed_origins(self) -> tuple[str, ...]:
        if self.thelounge_port is None:
            raise RuntimeError("The Lounge port must be assigned before resolver CORS origins are built")
        origins = [f"http://{self.thelounge_host}:{self.thelounge_port}"]
        if self.thelounge_host == "127.0.0.1":
            origins.append(f"http://localhost:{self.thelounge_port}")
        elif self.thelounge_host == "localhost":
            origins.append(f"http://127.0.0.1:{self.thelounge_port}")
        return tuple(dict.fromkeys(origins))

    def _ensure_refinement_store(self) -> ConversationRefinementStore:
        if self.refinement_store is None:
            self.refinement_store = ConversationRefinementStore(self.refinement_db_path)
        return self.refinement_store

    def _start_thelounge_process(self) -> None:
        if self.thelounge_process is not None and self.thelounge_process.poll() is None:
            return
        if self.port is None:
            raise RuntimeError("Ergo must be started before The Lounge")
        self._require_thelounge_build()
        env = os.environ.copy()
        env.update(self.thelounge_environment())
        env["THELOUNGE_HOME"] = str((self.thelounge_runtime_dir / "home").resolve())
        self.thelounge_runtime_dir.mkdir(parents=True, exist_ok=True)
        resolved_port = self.thelounge_port or find_free_port()
        self.thelounge_port = resolved_port
        self._thelounge_stdout_file = self.thelounge_stdout_path.open("w", encoding="utf-8")
        self._thelounge_stderr_file = self.thelounge_stderr_path.open("w", encoding="utf-8")
        self.thelounge_process = subprocess.Popen(
            self._thelounge_command(),
            cwd=self.thelounge_root,
            stdout=self._thelounge_stdout_file,
            stderr=self._thelounge_stderr_file,
            env=env,
            text=True,
        )
        wait_for_tcp(self.thelounge_host, resolved_port, timeout=self.startup_timeout)

    def _require_thelounge_build(self) -> None:
        server_entry = self.thelounge_root / "dist" / "server" / "index.js"
        if not server_entry.exists():
            raise RuntimeError(f"The Lounge build is missing at {server_entry}; run `yarn build` in {self.thelounge_root}")

    def _thelounge_command(self) -> list[str]:
        if self.port is None or self.thelounge_port is None:
            raise RuntimeError("Ergo and The Lounge ports must be assigned before building command")
        return [
            "node",
            "index.js",
            "-c",
            f"host={self.thelounge_host}",
            "-c",
            f"port={self.thelounge_port}",
            "-c",
            "public=true",
            "-c",
            "lockNetwork=true",
            "-c",
            "defaults.name=AlienHand",
            "-c",
            "defaults.host=127.0.0.1",
            "-c",
            f"defaults.port={self.port}",
            "-c",
            "defaults.tls=false",
            "-c",
            "defaults.rejectUnauthorized=false",
            "-c",
            "defaults.nick=alienhanduser%%",
            "start",
        ]


def run_app_lifecycle_proof(
    root: str | Path,
    *,
    ergo_root: str | Path | None = None,
    app_id: int = 1,
    nick: str = "alienhandagent",
    text: str = "hello app-owned Ergo",
    port: int | None = None,
    timeout: float = 30.0,
) -> JsonDict:
    chat_root = Path(root)
    channel_uuid = uuid4().hex
    with AlienHandChatService(
        chat_root,
        ergo_root=ergo_root,
        app_id=app_id,
        nick=nick,
        port=port,
        startup_timeout=timeout,
        build_timeout=timeout,
    ) as service:
        published = service.publish_text(text, channel_uuid=channel_uuid, metadata={"proof": "app_lifecycle"})
        running_port = service.port
        payload_resolver_base_url = service.payload_resolver_base_url
        payload_resolver_port = service.payload_resolver_port
        config_path = service.config_path
        binary_path = service.binary_path
        process_started = service.process is not None and service.process.poll() is None
        payload_resolver_started = service.payload_http_server is not None

    replayed = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    resolved_payloads = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]
    return {
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "irc_channel": irc_channel_name(channel_uuid),
        "message_uuid": published.envelope.message_uuid,
        "envelope": published.envelope.to_line(),
        "ergo_root": str((Path(ergo_root) if ergo_root else default_ergo_root()).resolve()),
        "ergo_port": running_port,
        "payload_resolver_base_url": payload_resolver_base_url,
        "payload_resolver_port": payload_resolver_port,
        "config_path": str(config_path.resolve()) if config_path else None,
        "binary_path": str(binary_path.resolve()) if binary_path else None,
        "app_lifecycle_started": process_started,
        "payload_resolver_started": payload_resolver_started,
        "app_lifecycle_stopped": True,
        "payload_resolver_stopped": service.payload_http_server is None,
        "history_events": len(replayed),
        "resolved_payloads": len(resolved_payloads),
        "cold_replay_ok": len(resolved_payloads) == 1 and payload_resolver_started and service.payload_http_server is None,
        "root": str(chat_root.resolve()),
    }


def run_app_refinement_lifecycle_proof(
    root: str | Path,
    *,
    app_id: int = 1,
    text: str = "hello searchable refinement",
) -> JsonDict:
    chat_root = Path(root)
    channel_uuid = uuid4().hex
    live_service = AlienHandChatService(chat_root, app_id=app_id, start_payload_resolver=False)
    live_service.publisher = EnvelopeOutbox(chat_root / "irc_outbox.jsonl")
    try:
        published = live_service.publish_text(
            text,
            channel_uuid=channel_uuid,
            nick="user",
            sender_type="user",
            metadata={"proof": "app_refinement_lifecycle"},
        )
        live_store = live_service._ensure_refinement_store()
        live_hits = live_store.search("searchable")
        live_blocks = live_store.count("conversation_blocks")
        live_db_path = live_service.refinement_db_path
    finally:
        live_service.stop()

    cold_db_path = chat_root / "cold-refinement.sqlite3"
    cold_service = AlienHandChatService(
        chat_root,
        app_id=app_id,
        start_payload_resolver=False,
        refinement_db_path=cold_db_path,
    )
    try:
        cold_blocks = cold_service.sync_refinement_channel(channel_uuid)
        cold_store = cold_service._ensure_refinement_store()
        cold_hits = cold_store.search("searchable")
        cold_block_count = cold_store.count("conversation_blocks")
    finally:
        cold_service.stop()

    expected_block_id = f"message:{published.envelope.message_uuid}"
    return {
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "message_uuid": published.envelope.message_uuid,
        "live_database": str(live_db_path.resolve()),
        "cold_database": str(cold_db_path.resolve()),
        "live_blocks": live_blocks,
        "cold_blocks": cold_block_count,
        "cold_imported_blocks": len(cold_blocks),
        "live_search_hits": len(live_hits),
        "cold_search_hits": len(cold_hits),
        "expected_block_id": expected_block_id,
        "cold_block_ids": [block.block_id for block in cold_blocks],
        "app_refinement_lifecycle_ok": live_blocks == 1
        and cold_block_count == 1
        and len(cold_blocks) == 1
        and len(live_hits) == 1
        and len(cold_hits) == 1
        and cold_blocks[0].block_id == expected_block_id,
        "root": str(chat_root.resolve()),
    }


def run_user_client_proof(
    root: str | Path,
    *,
    ergo_root: str | Path | None = None,
    app_id: int = 1,
    agent_nick: str = "alienhandagent",
    user_nick: str = "alienhanduser",
    text: str = "hello user client",
    port: int | None = None,
    timeout: float = 30.0,
) -> JsonDict:
    chat_root = Path(root)
    channel_uuid = uuid4().hex
    received: ReceivedIRCEnvelope | None = None
    with AlienHandChatService(
        chat_root,
        ergo_root=ergo_root,
        app_id=app_id,
        nick=agent_nick,
        port=port,
        startup_timeout=timeout,
        build_timeout=timeout,
    ) as service:
        process_started = service.process is not None and service.process.poll() is None
        receiver = IRCEnvelopeReceiver("127.0.0.1", service.port or 0, user_nick, timeout=5.0)
        try:
            receiver.join(channel_uuid)
            published = service.publish_text(text, channel_uuid=channel_uuid, metadata={"proof": "user_client"})
            received = receiver.wait_for_envelope(channel_uuid, timeout=timeout)
            resolved_payload = service.resolver.resolve_render_model(received.envelope.message_uuid)
        finally:
            receiver.close()
        running_port = service.port

    replayed = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    resolved_payloads = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]
    received_line = received.envelope.to_line() if received else ""
    return {
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "irc_channel": irc_channel_name(channel_uuid),
        "message_uuid": published.envelope.message_uuid,
        "published_envelope": published.envelope.to_line(),
        "received_envelope": received_line,
        "received_raw_line": received.raw_line if received else "",
        "received_matches_published": received_line == published.envelope.to_line(),
        "payload_resolved": resolved_payload.get("message_uuid") == published.envelope.message_uuid,
        "payload_text": resolved_payload.get("content", {}).get("text"),
        "ergo_root": str((Path(ergo_root) if ergo_root else default_ergo_root()).resolve()),
        "ergo_port": running_port,
        "app_lifecycle_started": process_started,
        "app_lifecycle_stopped": True,
        "user_client_received": received is not None,
        "history_events": len(replayed),
        "resolved_payloads": len(resolved_payloads),
        "cold_replay_ok": len(resolved_payloads) == 1,
        "root": str(chat_root.resolve()),
    }


def run_ergo_lifecycle_proof(
    root: str | Path,
    *,
    ergo_root: str | Path | None = None,
    app_id: int = 1,
    nick: str = "alienhandagent",
    text: str = "hello Ergo",
    port: int | None = None,
    timeout: float = 30.0,
) -> JsonDict:
    chat_root = Path(root)
    runtime_dir = chat_root / "ergo-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    resolved_ergo_root = Path(ergo_root) if ergo_root else default_ergo_root()
    resolved_port = port or find_free_port()
    config_path = write_local_ergo_config(resolved_ergo_root, runtime_dir, resolved_port)
    binary_path = build_ergo_binary(resolved_ergo_root, runtime_dir, timeout=timeout)
    stdout_path = runtime_dir / "ergo.stdout.log"
    stderr_path = runtime_dir / "ergo.stderr.log"
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(binary_path), "run", "--conf", str(config_path), "--quiet"],
        cwd=resolved_ergo_root,
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
    )
    channel_uuid = uuid4().hex
    published: PublishedMessage | None = None
    try:
        wait_for_tcp("127.0.0.1", resolved_port, timeout=timeout)
        publisher = IRCNetworkPublisher("127.0.0.1", resolved_port, nick, timeout=5.0)
        try:
            published = commit_message(
                app_id=app_id,
                channel_uuid=channel_uuid,
                nick=nick,
                sender_type="ai_agent",
                payload_kind="text",
                content={"text": text},
                store=PayloadStore(chat_root),
                history=ChannelJSONLHistory(chat_root),
                publisher=publisher,
            )
        finally:
            publisher.close()
        replayed = replay_channel(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
        resolved_payloads = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]
        return {
            "app_id": app_id,
            "channel_uuid": channel_uuid,
            "irc_channel": irc_channel_name(channel_uuid),
            "message_uuid": published.envelope.message_uuid,
            "envelope": published.envelope.to_line(),
            "ergo_root": str(resolved_ergo_root.resolve()),
            "ergo_port": resolved_port,
            "ergo_started": True,
            "history_events": len(replayed),
            "resolved_payloads": len(resolved_payloads),
            "cold_replay_ok": len(resolved_payloads) == 1,
            "root": str(chat_root.resolve()),
        }
    finally:
        stop_process(process, timeout=5.0)
        stdout_file.close()
        stderr_file.close()


def commit_message(
    *,
    app_id: int,
    channel_uuid: str,
    nick: str,
    sender_type: str,
    payload_kind: str,
    content: JsonDict,
    store: PayloadStore,
    history: ChannelJSONLHistory,
    publisher: EnvelopePublisher,
    event_type: str = "message",
    frames: tuple[JsonDict, ...] = (),
    metadata: JsonDict | None = None,
    message_uuid: str | None = None,
    timestamp: str | None = None,
) -> PublishedMessage:
    created_at = timestamp or utc_timestamp_ms()
    resolved_channel_uuid = normalize_channel_uuid(channel_uuid)
    resolved_message_uuid = message_uuid or str(uuid4())
    payload = PayloadObject(
        message_uuid=resolved_message_uuid,
        app_id=app_id,
        channel_uuid=resolved_channel_uuid,
        sender=nick,
        sender_type=sender_type,
        event_type=event_type,
        payload_kind=payload_kind,
        created_at=created_at,
        content=content,
        frames=frames,
        metadata=metadata or {},
    )
    envelope = IRCMessageEnvelope(
        app_id=app_id,
        channel_uuid=resolved_channel_uuid,
        message_uuid=resolved_message_uuid,
        nick=nick,
        timestamp=created_at,
    )
    store.write(payload)
    history_event = {
        "app_id": app_id,
        "channel_uuid": resolved_channel_uuid,
        "event_type": event_type,
        "message_uuid": resolved_message_uuid,
        "nick": nick,
        "sender_type": sender_type,
        "timestamp": created_at,
        "envelope": envelope.to_dict(),
    }
    history.append(resolved_channel_uuid, history_event)
    publisher.publish(envelope)
    return PublishedMessage(envelope=envelope, payload=payload, history_event=history_event)


def replay_channel(history: ChannelJSONLHistory, resolver: PayloadResolver, channel_uuid: str) -> list[JsonDict]:
    replayed = []
    for event in history.load(normalize_channel_uuid(channel_uuid)):
        message_uuid = str(event.get("message_uuid", ""))
        replayed.append(
            {
                "event": event,
                "payload": resolver.resolve_render_model(message_uuid) if message_uuid else payload_error("", "missing_message_uuid"),
            }
        )
    return replayed


def replay_channel_chunks(
    history: ChannelJSONLHistory,
    resolver: PayloadResolver,
    channel_uuid: str,
    *,
    chunk_size: int = 50,
    limit: int | None = None,
    direction: str = RECENT_FIRST_BACKFILL,
    event_types: tuple[str, ...] | None = None,
) -> list[JsonDict]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1 when provided")
    if direction not in {RECENT_FIRST_BACKFILL, OLDEST_FIRST_RECONSTRUCT}:
        raise ValueError(f"unsupported replay direction: {direction}")

    resolved_channel_uuid = normalize_channel_uuid(channel_uuid)
    events = history.load(resolved_channel_uuid)
    if event_types is not None:
        allowed = set(event_types)
        events = [event for event in events if event.get("event_type") in allowed]
    if limit is not None:
        events = events[-limit:] if direction == RECENT_FIRST_BACKFILL else events[:limit]

    chunks: list[JsonDict] = []
    if direction == RECENT_FIRST_BACKFILL:
        end = len(events)
        while end > 0:
            start = max(0, end - chunk_size)
            chunk_events = events[start:end]
            chunks.append(_replay_chunk(resolver, resolved_channel_uuid, direction, len(chunks), chunk_events, has_more=start > 0))
            end = start
    else:
        for start in range(0, len(events), chunk_size):
            chunk_events = events[start : start + chunk_size]
            chunks.append(
                _replay_chunk(
                    resolver,
                    resolved_channel_uuid,
                    direction,
                    len(chunks),
                    chunk_events,
                    has_more=start + chunk_size < len(events),
                )
            )
    return chunks


def payload_to_render_model(payload: PayloadObject | JsonDict) -> JsonDict:
    data = payload.to_dict() if isinstance(payload, PayloadObject) else dict(payload)
    sender_type = str(data.get("sender_type", "service"))
    event_type = str(data.get("event_type", "message"))
    payload_kind = str(data.get("payload_kind", "system"))
    return {
        "message_uuid": data.get("message_uuid", ""),
        "channel_uuid": data.get("channel_uuid", ""),
        "sender": data.get("sender", ""),
        "sender_type": sender_type,
        "event_type": event_type,
        "payload_kind": payload_kind,
        "created_at": data.get("created_at", ""),
        "orientation": _render_orientation(sender_type, event_type),
        "status": "payload_error" if event_type == "payload_error" else "resolved",
        "content": data.get("content", {}),
        "frames": _normalize_render_frames(data.get("frames", ()), payload_kind),
        "metadata": data.get("metadata", {}),
    }


def replay_channel_render_models(history: ChannelJSONLHistory, resolver: PayloadResolver, channel_uuid: str) -> list[JsonDict]:
    return [payload_to_render_model(row["payload"]) for row in replay_channel(history, resolver, channel_uuid)]


def parse_history_command(
    text: str,
    *,
    default_messages: int = DEFAULT_HISTORY_COMMAND_MESSAGES,
    default_chunk_size: int = DEFAULT_HISTORY_COMMAND_CHUNK_SIZE,
    max_messages: int = MAX_HISTORY_COMMAND_MESSAGES,
    max_chunk_size: int = MAX_HISTORY_COMMAND_CHUNK_SIZE,
) -> JsonDict | None:
    stripped = text.strip()
    tokens = stripped.split()
    if len(tokens) < 2 or tokens[0].lower() != "!ah" or tokens[1].lower() != "history":
        return None

    mode = "last_messages"
    messages: int | None = default_messages
    message_count_seen = False
    chunk_size = default_chunk_size
    index = 2
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if lowered == "all":
            if message_count_seen:
                raise ValueError("history command cannot combine all with a message count")
            mode = "all_messages"
            messages = None
            index += 1
        elif lowered in {"chunk", "--chunk", "chunk-size", "--chunk-size"}:
            index += 1
            if index >= len(tokens):
                raise ValueError("history command chunk size is missing")
            chunk_size = _parse_positive_int(tokens[index], "history command chunk size")
            index += 1
        elif lowered.startswith("chunk=") or lowered.startswith("--chunk="):
            chunk_size = _parse_positive_int(token.split("=", 1)[1], "history command chunk size")
            index += 1
        elif lowered.startswith("chunk-size=") or lowered.startswith("--chunk-size="):
            chunk_size = _parse_positive_int(token.split("=", 1)[1], "history command chunk size")
            index += 1
        elif token.isdigit():
            if mode == "all_messages":
                raise ValueError("history command cannot combine all with a message count")
            messages = _parse_positive_int(token, "history command message count")
            message_count_seen = True
            index += 1
        else:
            raise ValueError(f"unsupported history command token: {token}")

    if messages is not None and messages > max_messages:
        raise ValueError(f"history command message count exceeds {max_messages}")
    if chunk_size > max_chunk_size:
        raise ValueError(f"history command chunk size exceeds {max_chunk_size}")

    return {
        "syntax": AH_HISTORY_COMMAND_SYNTAX,
        "command_text": stripped,
        "mode": mode,
        "messages": messages,
        "chunk_size": chunk_size,
        "direction": RECENT_FIRST_BACKFILL,
        "event_types": ["message"],
    }


def handle_history_command(
    command_text: str,
    *,
    app_id: int,
    channel_uuid: str,
    nick: str,
    requester_type: str,
    store: PayloadStore,
    history: ChannelJSONLHistory,
    resolver: PayloadResolver,
    publisher: EnvelopePublisher,
    metadata: JsonDict | None = None,
) -> JsonDict | None:
    request = parse_history_command(command_text)
    if request is None:
        return None

    published = record_history_request(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick=nick,
        requester_type=requester_type,
        request=request,
        store=store,
        history=history,
        publisher=publisher,
        metadata=metadata,
    )
    chunks = replay_channel_chunks(
        history,
        resolver,
        channel_uuid,
        chunk_size=int(request["chunk_size"]),
        limit=request["messages"],
        direction=str(request["direction"]),
        event_types=tuple(request["event_types"]),
    )
    payloads = [row["payload"] for chunk in chunks for row in chunk["events"]]
    return {
        "request": request,
        "history_request_uuid": published.envelope.message_uuid,
        "history_request_envelope": published.envelope.to_line(),
        "history_request_payload": published.payload.to_dict(),
        "chunks": chunks,
        "chunk_count": len(chunks),
        "chunk_lengths": [chunk["event_count"] for chunk in chunks],
        "resolved_payloads": len([payload for payload in payloads if payload.get("event_type") != "payload_error"]),
        "payload_errors": len([payload for payload in payloads if payload.get("event_type") == "payload_error"]),
    }


def record_history_request(
    *,
    app_id: int,
    channel_uuid: str,
    nick: str,
    requester_type: str,
    request: JsonDict,
    store: PayloadStore,
    history: ChannelJSONLHistory,
    publisher: EnvelopePublisher,
    metadata: JsonDict | None = None,
) -> PublishedMessage:
    return commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick=nick,
        sender_type=requester_type,
        payload_kind="system",
        event_type="history_request",
        content={"request": request},
        metadata=metadata,
        store=store,
        history=history,
        publisher=publisher,
    )


def run_chat_truth_test(root: str | Path) -> JsonDict:
    chat_root = Path(root)
    store = PayloadStore(chat_root)
    history = ChannelJSONLHistory(chat_root)
    outbox = EnvelopeOutbox(chat_root / "irc_outbox.jsonl")
    channel_uuid = uuid4().hex
    app_id = 1

    first = commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="user",
        sender_type="user",
        payload_kind="text",
        content={"text": "hello AlienHand"},
        store=store,
        history=history,
        publisher=outbox,
    )
    second = commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="agent",
        sender_type="ai_agent",
        payload_kind="text",
        content={"text": "hello user"},
        store=store,
        history=history,
        publisher=outbox,
    )

    missing_uuid = str(uuid4())
    missing_envelope = IRCMessageEnvelope(
        app_id=app_id,
        channel_uuid=channel_uuid,
        message_uuid=missing_uuid,
        nick="service",
        timestamp=utc_timestamp_ms(),
    )
    history.append(
        channel_uuid,
        {
            "app_id": app_id,
            "channel_uuid": channel_uuid,
            "event_type": "message",
            "message_uuid": missing_uuid,
            "nick": "service",
            "sender_type": "service",
            "timestamp": missing_envelope.timestamp,
            "envelope": missing_envelope.to_dict(),
        },
    )

    cold_history = ChannelJSONLHistory(chat_root)
    cold_resolver = PayloadResolver(PayloadStore(chat_root))
    replayed = replay_channel(cold_history, cold_resolver, channel_uuid)
    payload_errors = [row for row in replayed if row["payload"].get("event_type") == "payload_error"]
    resolved_messages = [row for row in replayed if row["payload"].get("event_type") != "payload_error"]

    return {
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "messages_committed": [first.envelope.message_uuid, second.envelope.message_uuid],
        "envelopes_published": len(outbox.lines),
        "history_events": len(cold_history.load(channel_uuid)),
        "replayed": len(replayed),
        "resolved_payloads": len(resolved_messages),
        "payload_errors": len(payload_errors),
        "cold_replay_ok": len(resolved_messages) == 2 and len(payload_errors) == 1,
        "root": str(chat_root.resolve()),
    }


def run_history_replay_proof(
    root: str | Path,
    *,
    app_id: int = 1,
    message_count: int = 5,
    chunk_size: int = 2,
    limit: int = 4,
    command_text: str | None = None,
) -> JsonDict:
    chat_root = Path(root)
    store = PayloadStore(chat_root)
    history = ChannelJSONLHistory(chat_root)
    outbox = EnvelopeOutbox(chat_root / "irc_outbox.jsonl")
    channel_uuid = uuid4().hex
    committed = []

    for index in range(1, message_count + 1):
        committed.append(
            commit_message(
                app_id=app_id,
                channel_uuid=channel_uuid,
                nick="agent",
                sender_type="ai_agent",
                payload_kind="text",
                content={"text": f"message {index}"},
                store=store,
                history=history,
                publisher=outbox,
            )
        )

    resolved_command_text = command_text or f"!ah history {limit} chunk {chunk_size}"
    command_result = handle_history_command(
        resolved_command_text,
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="user",
        requester_type="user",
        store=store,
        history=history,
        resolver=PayloadResolver(store),
        publisher=outbox,
        metadata={"proof": "history_replay"},
    )
    if command_result is None:
        raise ValueError(f"not an AlienHand history command: {resolved_command_text}")
    chunks = command_result["chunks"]
    payloads = [row["payload"] for chunk in chunks for row in chunk["events"]]
    history_events = ChannelJSONLHistory(chat_root).load(channel_uuid)
    return {
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "history_command": resolved_command_text,
        "history_command_request": command_result["request"],
        "messages_committed": [message.envelope.message_uuid for message in committed],
        "history_request_uuid": command_result["history_request_uuid"],
        "history_events": len(history_events),
        "outbox_envelopes": len(outbox.lines),
        "chunk_count": command_result["chunk_count"],
        "chunk_lengths": command_result["chunk_lengths"],
        "first_chunk_texts": [row["payload"]["content"]["text"] for row in chunks[0]["events"]] if chunks else [],
        "resolved_payloads": len([payload for payload in payloads if payload.get("event_type") != "payload_error"]),
        "request_recorded": any(event.get("event_type") == "history_request" for event in history_events),
        "history_command_ok": command_result["request"]["mode"] in {"last_messages", "all_messages"}
        and command_result["history_request_payload"]["event_type"] == "history_request",
        "cold_replay_ok": len(payloads) == min(command_result["request"]["messages"] or message_count, message_count),
        "root": str(chat_root.resolve()),
    }


def run_render_model_proof(root: str | Path, *, app_id: int = 1) -> JsonDict:
    chat_root = Path(root)
    store = PayloadStore(chat_root)
    history = ChannelJSONLHistory(chat_root)
    outbox = EnvelopeOutbox(chat_root / "irc_outbox.jsonl")
    channel_uuid = uuid4().hex

    user_message = commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="user",
        sender_type="user",
        payload_kind="text",
        content={"text": "hello agent"},
        store=store,
        history=history,
        publisher=outbox,
    )
    agent_message = commit_message(
        app_id=app_id,
        channel_uuid=channel_uuid,
        nick="agent",
        sender_type="ai_agent",
        payload_kind="mixed",
        content={"text": "code and image frames"},
        frames=(
            {"kind": "code", "language": "python", "code": "print('alienhand')"},
            {"kind": "image", "source": "payloads/example.png", "alt": "example image"},
        ),
        store=store,
        history=history,
        publisher=outbox,
    )

    missing_uuid = str(uuid4())
    missing_envelope = IRCMessageEnvelope(
        app_id=app_id,
        channel_uuid=channel_uuid,
        message_uuid=missing_uuid,
        nick="service",
        timestamp=utc_timestamp_ms(),
    )
    history.append(
        channel_uuid,
        {
            "app_id": app_id,
            "channel_uuid": channel_uuid,
            "event_type": "message",
            "message_uuid": missing_uuid,
            "nick": "service",
            "sender_type": "service",
            "timestamp": missing_envelope.timestamp,
            "envelope": missing_envelope.to_dict(),
        },
    )

    rows = replay_channel_render_models(ChannelJSONLHistory(chat_root), PayloadResolver(PayloadStore(chat_root)), channel_uuid)
    agent_row = next(row for row in rows if row["message_uuid"] == agent_message.envelope.message_uuid)
    payload_error_rows = [row for row in rows if row["status"] == "payload_error"]
    return {
        "app_id": app_id,
        "channel_uuid": channel_uuid,
        "user_message_uuid": user_message.envelope.message_uuid,
        "agent_message_uuid": agent_message.envelope.message_uuid,
        "render_rows": len(rows),
        "orientation_sequence": [row["orientation"] for row in rows],
        "agent_frame_kinds": [frame["kind"] for frame in agent_row["frames"]],
        "payload_error_rows": len(payload_error_rows),
        "render_model_ok": [row["orientation"] for row in rows] == ["left", "right", "system"]
        and [frame["kind"] for frame in agent_row["frames"]] == ["code", "image"]
        and len(payload_error_rows) == 1,
        "root": str(chat_root.resolve()),
    }


def default_ergo_root() -> Path:
    return Path(__file__).resolve().parents[2] / "Ergo"


def default_thelounge_root() -> Path:
    return Path(__file__).resolve().parents[2] / "thelounge"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_local_ergo_config(ergo_root: str | Path, runtime_dir: str | Path, port: int) -> Path:
    source = Path(ergo_root) / "default.yaml"
    target_dir = Path(runtime_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    config = make_local_ergo_config(
        source.read_text(encoding="utf-8"),
        port=port,
        datastore_path=target_dir / "ircd.db",
        lock_path=target_dir / "ircd.lock",
    )
    target = (target_dir / "ircd.yaml").resolve()
    target.write_text(config, encoding="utf-8")
    return target


def make_local_ergo_config(default_config: str, *, port: int, datastore_path: str | Path, lock_path: str | Path) -> str:
    output = []
    skipping_listeners = False
    for line in default_config.splitlines():
        if line == "    listeners:":
            output.append(line)
            output.append(f'        "127.0.0.1:{port}":')
            skipping_listeners = True
            continue
        if skipping_listeners:
            if line.startswith("    unix-bind-mode:"):
                skipping_listeners = False
                output.append(line)
            continue
        if line.startswith("lock-file:"):
            output.append(f'lock-file: "{_yaml_path(lock_path)}"')
        elif line == "    path: ircd.db":
            output.append(f'    path: "{_yaml_path(datastore_path)}"')
        else:
            output.append(line)
    output.append("")
    return "\n".join(output)


def build_ergo_binary(ergo_root: str | Path, runtime_dir: str | Path, *, timeout: float = 120.0) -> Path:
    target = (Path(runtime_dir) / ("ergo.exe" if os.name == "nt" else "ergo")).resolve()
    result = subprocess.run(
        ["go", "build", "-o", str(target), "."],
        cwd=Path(ergo_root),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Ergo build failed: {result.stderr.strip() or result.stdout.strip()}")
    return target


def wait_for_tcp(host: str, port: int, *, timeout: float = 30.0) -> None:
    deadline = time() + timeout
    last_error: OSError | None = None
    while time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as error:
            last_error = error
    raise TimeoutError(f"Timed out waiting for TCP {host}:{port}: {last_error}")


def stop_process(process: subprocess.Popen, *, timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def irc_channel_name(channel_uuid: str) -> str:
    return f"#{normalize_channel_uuid(channel_uuid)}"


def normalize_channel_uuid(channel_uuid: str | UUID) -> str:
    raw = str(channel_uuid).strip()
    if raw.startswith("#"):
        raw = raw[1:]
    try:
        return UUID(raw).hex
    except ValueError as error:
        raise ValueError("channel UUID must be a 32-character hexadecimal UUID value") from error


def payload_error(message_uuid: str, reason: str) -> JsonDict:
    return {
        "message_uuid": message_uuid,
        "sender": "payload_resolver",
        "sender_type": "service",
        "event_type": "payload_error",
        "payload_kind": "system",
        "created_at": utc_timestamp_ms(),
        "content": {"reason": reason},
        "frames": [],
        "metadata": {},
    }


def utc_timestamp_ms(now: datetime | None = None) -> str:
    value = now.astimezone(UTC) if now else datetime.now(UTC)
    return f"{value:%Y%m%dT%H%M%S}.{value.microsecond // 1000:03d}Z"


def unix_millis() -> int:
    return int(time() * 1000)


def _parse_wire_tokens(tokens: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            raise ValueError(f"invalid envelope token: {token}")
        key, value = token.split("=", 1)
        values[key] = value
    required = {"a", "c", "m", "n", "t"}
    missing = required - values.keys()
    if missing:
        raise ValueError(f"missing envelope fields: {sorted(missing)}")
    return values


def _parse_positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a positive integer") from error
    if parsed < 1:
        raise ValueError(f"{label} must be a positive integer")
    return parsed


def _require_wire_token(key: str, value: str) -> None:
    if not value:
        raise ValueError(f"empty envelope field: {key}")
    if any(character.isspace() for character in value):
        raise ValueError(f"envelope field contains whitespace: {key}")


def _line_confirms_join(line: str, target: str) -> bool:
    parts = line.split()
    normalized_target = target.lower()
    if len(parts) >= 3 and parts[1].upper() == "JOIN":
        return parts[2].lstrip(":").lower() == normalized_target
    if len(parts) >= 4 and parts[1] == "366":
        return any(part.lstrip(":").lower() == normalized_target for part in parts[3:])
    return False


def _parse_irc_privmsg(line: str) -> JsonDict | None:
    rest = line
    prefix = ""
    if rest.startswith(":"):
        if " " not in rest:
            return None
        prefix, rest = rest[1:].split(" ", 1)
    if " :" in rest:
        before, message = rest.split(" :", 1)
    else:
        before = rest
        message = ""
    parts = before.split()
    if len(parts) < 2 or parts[0].upper() != "PRIVMSG":
        return None
    return {"prefix": prefix, "target": parts[1], "message": message}


def _replay_chunk(
    resolver: PayloadResolver,
    channel_uuid: str,
    direction: str,
    index: int,
    events: list[JsonDict],
    *,
    has_more: bool,
) -> JsonDict:
    replayed = []
    for event in events:
        message_uuid = str(event.get("message_uuid", ""))
        replayed.append(
            {
                "event": event,
                "payload": resolver.resolve_render_model(message_uuid) if message_uuid else payload_error("", "missing_message_uuid"),
            }
        )
    return {
        "channel_uuid": channel_uuid,
        "direction": direction,
        "chunk_index": index,
        "event_count": len(replayed),
        "has_more": has_more,
        "events": replayed,
    }


def _render_orientation(sender_type: str, event_type: str) -> str:
    if event_type == "payload_error" or sender_type in {"system", "service"}:
        return "system"
    if sender_type == "ai_agent":
        return "right"
    return "left"


def _normalize_render_frames(frames: Any, payload_kind: str) -> list[JsonDict]:
    normalized = []
    for index, frame in enumerate(frames or []):
        frame_data = dict(frame)
        kind = str(frame_data.get("kind") or frame_data.get("type") or payload_kind or "mixed")
        render_frame = {"index": index, "kind": kind, "metadata": frame_data.get("metadata", {})}
        if kind == "code":
            render_frame.update(
                {
                    "language": frame_data.get("language", ""),
                    "text": frame_data.get("code", frame_data.get("text", "")),
                }
            )
        elif kind == "image":
            render_frame.update(
                {
                    "source": frame_data.get("source", frame_data.get("url", frame_data.get("path", ""))),
                    "alt": frame_data.get("alt", ""),
                    "mime_type": frame_data.get("mime_type", ""),
                }
            )
        elif kind in {"file", "link"}:
            render_frame.update(
                {
                    "source": frame_data.get("source", frame_data.get("url", frame_data.get("path", ""))),
                    "label": frame_data.get("label", frame_data.get("title", "")),
                    "mime_type": frame_data.get("mime_type", ""),
                }
            )
        else:
            render_frame["content"] = frame_data
        normalized.append(render_frame)
    return normalized


def _yaml_path(path: str | Path) -> str:
    return Path(path).resolve().as_posix().replace('"', '\\"')
