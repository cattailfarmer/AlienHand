from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import socket
import subprocess
from time import time
from typing import Any, Protocol
from uuid import UUID, uuid4


JsonDict = dict[str, Any]
IRC_BODY_BUDGET_BYTES = 400


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


def default_ergo_root() -> Path:
    return Path(__file__).resolve().parents[2] / "Ergo"


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


def _require_wire_token(key: str, value: str) -> None:
    if not value:
        raise ValueError(f"empty envelope field: {key}")
    if any(character.isspace() for character in value):
        raise ValueError(f"envelope field contains whitespace: {key}")


def _yaml_path(path: str | Path) -> str:
    return Path(path).resolve().as_posix().replace('"', '\\"')
