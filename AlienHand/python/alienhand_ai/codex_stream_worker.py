from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Any, Callable
from uuid import uuid4
from alienhand_ai.codex_stream_routing import route_codex_stream_request


SCHEMA_VERSION = 1


class CodexStreamWorkerStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CodexStreamWorkerStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def migrate(self) -> None:
        self.connection.executescript(SCHEMA_SQL)
        version = self.schema_version()
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"unsupported codex stream schema version: {version}")
        if version < SCHEMA_VERSION:
            self.connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_timestamp()),
            )
            self.connection.commit()

    def schema_version(self) -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
        return int(row["version"])

    def count(self, table: str) -> int:
        row = self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])

    def table_names(self) -> set[str]:
        rows = self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {row["name"] for row in rows}

    def enqueue_request(
        self,
        *,
        request_id: str | None,
        app_id: int,
        channel_uuid: str | None,
        requester_kind: str,
        requester_id: str,
        task_type: str,
        input_ref: str,
        evidence_refs: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
        model_budget_hint: str = "unknown",
        priority: str = "normal",
        deadline_ms: int | None = None,
        idempotency_key: str | None = None,
        status: str = "ready",
    ) -> dict[str, Any]:
        if not task_type or not requester_kind or not requester_id or not input_ref:
            raise ValueError("request requires task_type, requester_kind, requester_id, and input_ref")
        resolved_request_id = request_id or str(uuid4())
        now = utc_timestamp()
        with self.connection:
            if idempotency_key:
                existing_row = self.connection.execute(
                    """
                    SELECT * FROM codex_stream_requests
                    WHERE app_id = ? AND idempotency_key = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (app_id, idempotency_key),
                ).fetchone()
                if existing_row is not None:
                    return _request_row_to_dict(existing_row)
            self.connection.execute(
                """
                INSERT INTO codex_stream_requests(
                    request_id, app_id, channel_uuid, requester_kind, requester_id, task_type,
                    input_ref, evidence_refs_json, model_budget_hint, priority, deadline_ms,
                    idempotency_key, status, attempt_count, lease_id, error_ref,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_request_id,
                    app_id,
                    channel_uuid,
                    requester_kind,
                    requester_id,
                    task_type,
                    input_ref,
                    json.dumps(evidence_refs, sort_keys=True, separators=(",", ":")),
                    model_budget_hint,
                    priority,
                    deadline_ms,
                    idempotency_key,
                    status,
                    0,
                    None,
                    None,
                    now,
                    now,
                    None,
                ),
            )
        return {
            "request_id": resolved_request_id,
            "app_id": app_id,
            "channel_uuid": channel_uuid,
            "requester_kind": requester_kind,
            "requester_id": requester_id,
            "task_type": task_type,
            "input_ref": input_ref,
            "evidence_refs": list(evidence_refs),
            "model_budget_hint": model_budget_hint,
            "priority": priority,
            "deadline_ms": deadline_ms,
            "idempotency_key": idempotency_key,
            "status": status,
            "attempt_count": 0,
            "lease_id": None,
            "error_ref": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }

    def claim_next_request(self, *, worker_id: str, lease_ttl_ms: int = 30000) -> dict[str, Any] | None:
        now = utc_timestamp()
        expires_at = _add_milliseconds(now, lease_ttl_ms)
        with self.connection:
            self.connection.execute(
                """
                UPDATE codex_stream_leases
                SET status = 'expired', released_at = COALESCE(released_at, ?), heartbeat_at = ?
                WHERE status = 'active' AND expires_at < ?
                """,
                (now, now, now),
            )
            self.connection.execute(
                """
                UPDATE codex_stream_requests
                SET status = 'ready', lease_id = NULL, updated_at = ?
                WHERE status = 'claimed'
                  AND lease_id IN (
                    SELECT lease_id FROM codex_stream_leases
                    WHERE status = 'expired'
                  )
                """,
                (now,),
            )
            request_row = self.connection.execute(
                """
                SELECT * FROM codex_stream_requests
                WHERE status = 'ready'
                ORDER BY created_at ASC, request_id ASC
                LIMIT 1
                """,
            ).fetchone()
            if request_row is None:
                return None
            request_id = request_row["request_id"]
            attempt_row = self.connection.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) AS attempt_number
                FROM codex_stream_attempts
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            attempt_number = int(attempt_row["attempt_number"]) + 1
            attempt_id = str(uuid4())
            lease_id = str(uuid4())
            self.connection.execute(
                """
                INSERT INTO codex_stream_attempts(
                    attempt_id, request_id, attempt_number, worker_id, status,
                    lease_id, started_at, heartbeat_at, completed_at, error_ref, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
                """,
                (
                    attempt_id,
                    request_id,
                    attempt_number,
                    worker_id,
                    "running",
                    lease_id,
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO codex_stream_leases(
                    lease_id, attempt_id, worker_id, status, acquired_at, expires_at,
                    heartbeat_at, released_at, created_at
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, NULL, ?)
                """,
                (lease_id, attempt_id, worker_id, now, expires_at, now, now),
            )
            self.connection.execute(
                """
                UPDATE codex_stream_requests
                SET status = 'claimed', lease_id = ?, attempt_count = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (lease_id, attempt_number, now, request_id),
            )
        return {
            "request": _request_row_to_dict(request_row),
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "attempt_number": attempt_number,
            "worker_id": worker_id,
            "expires_at": expires_at,
        }

    def heartbeat_lease(self, *, lease_id: str, lease_ttl_ms: int = 30000) -> dict[str, Any] | None:
        now = utc_timestamp()
        expires_at = _add_milliseconds(now, lease_ttl_ms)
        with self.connection:
            lease_row = self.connection.execute(
                """
                SELECT * FROM codex_stream_leases
                WHERE lease_id = ?
                """,
                (lease_id,),
            ).fetchone()
            if lease_row is None or lease_row["status"] == "released":
                return None
            self.connection.execute(
                """
                UPDATE codex_stream_leases
                SET heartbeat_at = ?, expires_at = ?, status = 'active'
                WHERE lease_id = ? AND status <> 'released'
                """,
                (now, expires_at, lease_id),
            )
            request_id_row = self.connection.execute(
                """
                SELECT request_id FROM codex_stream_attempts WHERE attempt_id = ?
                """,
                (lease_row["attempt_id"],),
            ).fetchone()
            if request_id_row is not None:
                self.connection.execute(
                    """
                    UPDATE codex_stream_requests
                    SET updated_at = ?, status = 'claimed'
                    WHERE request_id = ?
                    """,
                    (now, request_id_row["request_id"]),
                )
            return _lease_row_to_dict(self.connection.execute(
                "SELECT * FROM codex_stream_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone())

    def complete_request(
        self,
        *,
        request_id: str,
        attempt_id: str,
        result_summary: str | None = None,
        result_ref: str | None = None,
        model_route: dict[str, Any] | None = None,
        justification_ref: str | None = None,
        artifact_refs: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        now = utc_timestamp()
        _ensure_request_exists(self.connection, request_id)
        response_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                """
                UPDATE codex_stream_attempts
                SET status = 'completed', completed_at = ?, heartbeat_at = ?
                WHERE attempt_id = ?
                """,
                (now, now, attempt_id),
            )
            self.connection.execute(
                """
                UPDATE codex_stream_leases
                SET status = 'released', released_at = ?, heartbeat_at = ?
                WHERE attempt_id = ?
                """,
                (now, now, attempt_id),
            )
            self.connection.execute(
                """
                INSERT INTO codex_stream_responses(
                    response_id, request_id, attempt_id, status, result_summary,
                    result_ref, model_route_json, justification_ref,
                    artifact_refs_json, created_at, completed_at
                ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    response_id,
                    request_id,
                    attempt_id,
                    result_summary,
                    result_ref,
                    json.dumps(model_route or {}, sort_keys=True, separators=(",", ":")),
                    justification_ref,
                    json.dumps(list(artifact_refs), sort_keys=True, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """
                UPDATE codex_stream_requests
                SET status = 'completed', updated_at = ?, completed_at = ?, lease_id = NULL
                WHERE request_id = ?
                """,
                (now, now, request_id),
            )
        return {
            "response_id": response_id,
            "request_id": request_id,
            "attempt_id": attempt_id,
            "status": "completed",
            "result_summary": result_summary,
            "result_ref": result_ref,
            "model_route": model_route or {},
            "justification_ref": justification_ref,
            "artifact_refs": list(artifact_refs),
            "created_at": now,
            "completed_at": now,
        }

    def fail_request(
        self,
        *,
        request_id: str,
        attempt_id: str,
        error_ref: str,
        retryable: bool = False,
        result_summary: str | None = None,
        status: str = "failed",
    ) -> dict[str, Any]:
        now = utc_timestamp()
        if status not in {"failed", "needs_human", "blocked"}:
            raise ValueError("status must be failed, needs_human, or blocked")
        _ensure_request_exists(self.connection, request_id)
        response_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                """
                UPDATE codex_stream_attempts
                SET status = ?, completed_at = ?, heartbeat_at = ?
                WHERE attempt_id = ?
                """,
                (status, now, now, attempt_id),
            )
            self.connection.execute(
                """
                UPDATE codex_stream_leases
                SET status = 'released', released_at = ?, heartbeat_at = ?
                WHERE attempt_id = ?
                """,
                (now, now, attempt_id),
            )
            self.connection.execute(
                """
                INSERT INTO codex_stream_responses(
                    response_id, request_id, attempt_id, status, result_summary,
                    result_ref, model_route_json, justification_ref,
                    artifact_refs_json, error_ref, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, NULL, '{}', NULL, '[]', ?, ?, ?)
                """,
                (
                    response_id,
                    request_id,
                    attempt_id,
                    "failed",
                    result_summary,
                    error_ref,
                    now,
                    now,
                ),
            )
            request_status = "ready" if retryable else status
            completed_at = None if retryable else now
            self.connection.execute(
                """
                UPDATE codex_stream_requests
                SET status = ?, error_ref = ?, updated_at = ?, completed_at = ?, lease_id = NULL
                WHERE request_id = ?
                """,
                (request_status, error_ref, now, completed_at, request_id),
            )
        return {
            "response_id": response_id,
            "request_id": request_id,
            "attempt_id": attempt_id,
            "status": "failed",
            "final_status": request_status,
            "error_ref": error_ref,
            "result_summary": result_summary,
            "retryable": retryable,
            "created_at": now,
            "completed_at": now,
        }

    def cancel_request(self, request_id: str, *, reason: str | None = None) -> dict[str, Any]:
        now = utc_timestamp()
        _ensure_request_exists(self.connection, request_id)
        response_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                """
                UPDATE codex_stream_requests
                SET status = 'cancelled', updated_at = ?, completed_at = ?, error_ref = ?, lease_id = NULL
                WHERE request_id = ?
                """,
                (now, now, reason, request_id),
            )
            self.connection.execute(
                """
                INSERT INTO codex_stream_responses(
                    response_id, request_id, attempt_id, status, result_summary,
                    result_ref, model_route_json, justification_ref,
                    artifact_refs_json, error_ref, created_at, completed_at
                ) VALUES (?, ?, NULL, 'cancelled', ?, NULL, '{}', NULL, '[]', ?, ?, ?)
                """,
                (response_id, request_id, reason, reason, now, now),
            )
        return {
            "response_id": response_id,
            "request_id": request_id,
            "status": "cancelled",
            "reason": reason,
            "created_at": now,
            "completed_at": now,
        }

    def request_status(self, request_id: str) -> dict[str, Any]:
        request_row = self.connection.execute(
            "SELECT * FROM codex_stream_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if request_row is None:
            raise KeyError(f"unknown request_id: {request_id}")
        attempt_rows = self.connection.execute(
            """
            SELECT attempt_id, attempt_number, status, lease_id, started_at,
                completed_at, heartbeat_at, error_ref, created_at
            FROM codex_stream_attempts
            WHERE request_id = ?
            ORDER BY attempt_number
            """,
            (request_id,),
        ).fetchall()
        lease_row = None
        if request_row["lease_id"] is not None:
            lease_row = self.connection.execute(
                "SELECT * FROM codex_stream_leases WHERE lease_id = ?",
                (request_row["lease_id"],),
            ).fetchone()
        response_rows = self.connection.execute(
            """
            SELECT response_id, status, result_summary, result_ref, model_route_json,
                   justification_ref, artifact_refs_json, error_ref, created_at, completed_at
            FROM codex_stream_responses
            WHERE request_id = ?
            ORDER BY created_at DESC
            """,
            (request_id,),
        ).fetchall()
        return {
            "request": _request_row_to_dict(request_row),
            "attempts": [_attempt_row_to_dict(row) for row in attempt_rows],
            "lease": _lease_row_to_dict(lease_row) if lease_row else None,
            "responses": [_response_row_to_dict(row) for row in response_rows],
        }


class CodexStreamWorker:
    def __init__(
        self,
        *,
        store: str | Path | CodexStreamWorkerStore,
        worker_id: str,
        executor: Callable[[dict[str, Any]], dict[str, Any]],
        poll_interval_ms: int = 100,
        lease_ttl_ms: int = 30000,
    ) -> None:
        self.store = store if isinstance(store, CodexStreamWorkerStore) else CodexStreamWorkerStore(store)
        self._owns_store = not isinstance(store, CodexStreamWorkerStore)
        self.worker_id = worker_id
        self._executor = executor
        self.poll_interval_ms = poll_interval_ms
        self.lease_ttl_ms = lease_ttl_ms
        self._stop_requested = False

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def stop(self) -> None:
        self._stop_requested = True

    def __enter__(self) -> "CodexStreamWorker":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
        self.close()

    def run_once(self) -> dict[str, Any] | None:
        if self._stop_requested:
            return None
        claim = self.store.claim_next_request(worker_id=self.worker_id, lease_ttl_ms=self.lease_ttl_ms)
        if claim is None:
            return None
        request = claim["request"]
        attempt_id = claim["attempt_id"]
        lease_id = claim["lease_id"]
        self.store.heartbeat_lease(lease_id=lease_id, lease_ttl_ms=self.lease_ttl_ms)
        try:
            result = self._executor(request)
            if not isinstance(result, dict):
                raise TypeError("executor must return dict")
            status = str(result.get("status", "completed")).lower()
            model_route = result.get("model_route")
            if model_route is None:
                route = route_codex_stream_request(
                    task_type=request["task_type"],
                    model_budget_hint=request["model_budget_hint"],
                )
                model_route = route.__dict__
            if status == "completed":
                self.store.complete_request(
                    request_id=request["request_id"],
                    attempt_id=attempt_id,
                    result_summary=result.get("result_summary"),
                    result_ref=result.get("result_ref"),
                    model_route=model_route,
                    justification_ref=result.get("justification_ref"),
                    artifact_refs=result.get("artifact_refs", []),
                )
                return {
                    "request_id": request["request_id"],
                    "attempt_id": attempt_id,
                    "outcome": "completed",
                }
            if status in {"failed", "needs_human", "blocked"}:
                self.store.fail_request(
                    request_id=request["request_id"],
                    attempt_id=attempt_id,
                    error_ref=result.get("error_ref", "executor_reported_error"),
                    retryable=bool(result.get("retryable", False)),
                    result_summary=result.get("result_summary"),
                    status=status,
                )
                return {
                    "request_id": request["request_id"],
                    "attempt_id": attempt_id,
                    "outcome": status,
                }
            self.store.fail_request(
                request_id=request["request_id"],
                attempt_id=attempt_id,
                error_ref=f"unrecognized worker status: {status}",
                retryable=False,
                result_summary="worker execution returned unrecognized status",
                status="blocked",
            )
            return {"request_id": request["request_id"], "attempt_id": attempt_id, "outcome": "blocked"}
        except Exception as error:
            self.store.fail_request(
                request_id=request["request_id"],
                attempt_id=attempt_id,
                error_ref=f"worker_exception:{type(error).__name__}:{error}",
                retryable=True,
                result_summary="executor exception",
                status="failed",
            )
            return {
                "request_id": request["request_id"],
                "attempt_id": attempt_id,
                "outcome": "failed",
                "error": str(error),
            }

    def run(self, *, max_iterations: int | None = None) -> dict[str, int | bool]:
        iterations = 0
        processed = 0
        while not self._stop_requested:
            result = self.run_once()
            iterations += 1
            if result is not None:
                processed += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            if result is None:
                time.sleep(self.poll_interval_ms / 1000)
        return {"iterations": iterations, "processed": processed, "stopped": self._stop_requested}


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"


def _ensure_request_exists(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM codex_stream_requests WHERE request_id = ?", (request_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown request_id: {request_id}")
    return row


def _add_milliseconds(timestamp: str, milliseconds: int) -> str:
    base = datetime.strptime(timestamp, "%Y%m%dT%H%M%S.%fZ")
    return (base + timedelta(milliseconds=milliseconds)).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"


def _request_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "request_id": row["request_id"],
        "app_id": row["app_id"],
        "channel_uuid": row["channel_uuid"],
        "requester_kind": row["requester_kind"],
        "requester_id": row["requester_id"],
        "task_type": row["task_type"],
        "input_ref": row["input_ref"],
        "evidence_refs": json.loads(row["evidence_refs_json"]),
        "model_budget_hint": row["model_budget_hint"],
        "priority": row["priority"],
        "deadline_ms": row["deadline_ms"],
        "idempotency_key": row["idempotency_key"],
        "status": row["status"],
        "attempt_count": row["attempt_count"],
        "lease_id": row["lease_id"],
        "error_ref": row["error_ref"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _attempt_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "attempt_id": row["attempt_id"],
        "attempt_number": row["attempt_number"],
        "status": row["status"],
        "lease_id": row["lease_id"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "heartbeat_at": row["heartbeat_at"],
        "error_ref": row["error_ref"],
        "created_at": row["created_at"],
    }


def _lease_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "lease_id": row["lease_id"],
        "attempt_id": row["attempt_id"],
        "worker_id": row["worker_id"],
        "status": row["status"],
        "acquired_at": row["acquired_at"],
        "expires_at": row["expires_at"],
        "heartbeat_at": row["heartbeat_at"],
        "released_at": row["released_at"],
        "created_at": row["created_at"],
    }


def _response_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "response_id": row["response_id"],
        "status": row["status"],
        "result_summary": row["result_summary"],
        "result_ref": row["result_ref"],
        "model_route": json.loads(row["model_route_json"]),
        "justification_ref": row["justification_ref"],
        "artifact_refs": json.loads(row["artifact_refs_json"]),
        "error_ref": row["error_ref"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codex_stream_requests (
    request_id TEXT PRIMARY KEY,
    app_id INTEGER NOT NULL,
    channel_uuid TEXT,
    requester_kind TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    input_ref TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    model_budget_hint TEXT NOT NULL DEFAULT 'unknown',
    priority TEXT NOT NULL DEFAULT 'normal',
    deadline_ms INTEGER,
    idempotency_key TEXT,
    status TEXT NOT NULL DEFAULT 'ready',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_id TEXT,
    error_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS codex_stream_responses (
    response_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES codex_stream_requests(request_id) ON DELETE CASCADE,
    attempt_id TEXT,
    status TEXT NOT NULL,
    result_summary TEXT,
    result_ref TEXT,
    model_route_json TEXT NOT NULL DEFAULT '{}',
    justification_ref TEXT,
    artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    error_ref TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS codex_stream_attempts (
    attempt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES codex_stream_requests(request_id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    worker_id TEXT,
    status TEXT NOT NULL,
    lease_id TEXT,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT,
    completed_at TEXT,
    error_ref TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (request_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS codex_stream_leases (
    lease_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES codex_stream_attempts(attempt_id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    heartbeat_at TEXT,
    released_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codex_stream_artifacts (
    artifact_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES codex_stream_requests(request_id) ON DELETE CASCADE,
    attempt_id TEXT,
    artifact_uri TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    mime_type TEXT,
    content_hash TEXT,
    size_bytes INTEGER,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_codex_stream_requests_status_created
ON codex_stream_requests(status, created_at);
CREATE INDEX IF NOT EXISTS idx_codex_stream_requests_app_channel
ON codex_stream_requests(app_id, channel_uuid);

CREATE INDEX IF NOT EXISTS idx_codex_stream_responses_request
ON codex_stream_responses(request_id);
CREATE INDEX IF NOT EXISTS idx_codex_stream_responses_status
ON codex_stream_responses(status, created_at);

CREATE INDEX IF NOT EXISTS idx_codex_stream_attempts_request
ON codex_stream_attempts(request_id);
CREATE INDEX IF NOT EXISTS idx_codex_stream_attempts_status
ON codex_stream_attempts(status);

CREATE INDEX IF NOT EXISTS idx_codex_stream_leases_status
ON codex_stream_leases(status, expires_at);

CREATE INDEX IF NOT EXISTS idx_codex_stream_artifacts_request
ON codex_stream_artifacts(request_id, artifact_kind);
CREATE INDEX IF NOT EXISTS idx_codex_stream_artifacts_attempt
ON codex_stream_artifacts(attempt_id);
"""


REQUIRED_TABLES = {
    "schema_migrations",
    "codex_stream_requests",
    "codex_stream_responses",
    "codex_stream_attempts",
    "codex_stream_leases",
    "codex_stream_artifacts",
}


def is_required_table_present(store: CodexStreamWorkerStore) -> bool:
    return REQUIRED_TABLES.issubset(store.table_names())


def run_codex_stream_schema_proof(root: str | Path) -> dict[str, object]:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    db_path = root_path / "codex_stream.sqlite3"
    with CodexStreamWorkerStore(db_path) as store:
        return {
            "database": str(db_path.resolve()),
            "schema_version": store.schema_version(),
            "tables_present": is_required_table_present(store),
            "migration_records": store.count("schema_migrations"),
            "requests": store.count("codex_stream_requests"),
            "responses": store.count("codex_stream_responses"),
            "attempts": store.count("codex_stream_attempts"),
            "leases": store.count("codex_stream_leases"),
            "artifacts": store.count("codex_stream_artifacts"),
            "schema_ok": store.schema_version() == SCHEMA_VERSION and is_required_table_present(store),
        }
