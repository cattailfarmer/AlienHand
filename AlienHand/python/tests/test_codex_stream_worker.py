from pathlib import Path
import sys
import tempfile
import unittest
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.codex_stream_worker import (
    SCHEMA_VERSION,
    CodexStreamWorkerStore,
    REQUIRED_TABLES,
    run_codex_stream_schema_proof,
    utc_timestamp,
)  # noqa: E402


class CodexStreamWorkerSchemaTests(unittest.TestCase):
    def test_store_migrates_to_current_schema_version(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                self.assertEqual(store.schema_version(), SCHEMA_VERSION)
                self.assertTrue(REQUIRED_TABLES.issubset(store.table_names()))

    def test_schema_proof_executes_and_reports_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = run_codex_stream_schema_proof(root / "proof")

            self.assertTrue(result["schema_ok"])
            self.assertEqual(result["schema_version"], SCHEMA_VERSION)
            self.assertTrue(result["tables_present"])
            self.assertEqual(result["migration_records"], 1)

    def test_cascade_delete_from_request_removes_child_records(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                now = utc_timestamp()
                store.connection.execute(
                    """
                    INSERT INTO codex_stream_requests(
                        request_id, app_id, requester_kind, requester_id, task_type,
                        input_ref, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "request-1",
                        7,
                        "system",
                        "alienhand",
                        "health_check",
                        "evidence://seed",
                        now,
                        now,
                    ),
                )
                store.connection.execute(
                    """
                    INSERT INTO codex_stream_attempts(
                        attempt_id, request_id, attempt_number, status,
                        started_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("attempt-1", "request-1", 1, "running", now, now),
                )
                store.connection.execute(
                    """
                    INSERT INTO codex_stream_leases(
                        lease_id, attempt_id, worker_id, acquired_at, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("lease-1", "attempt-1", "worker-a", now, now, now),
                )
                store.connection.execute(
                    """
                    INSERT INTO codex_stream_artifacts(
                        artifact_id, request_id, attempt_id, artifact_uri,
                        artifact_kind, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("artifact-1", "request-1", "attempt-1", "artifact://seed", "proof", now),
                )
                store.connection.execute(
                    """
                    INSERT INTO codex_stream_responses(
                        response_id, request_id, attempt_id, status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("response-1", "request-1", "attempt-1", "completed", now),
                )
                store.connection.commit()

                self.assertEqual(store.count("codex_stream_attempts"), 1)
                self.assertEqual(store.count("codex_stream_leases"), 1)
                self.assertEqual(store.count("codex_stream_artifacts"), 1)
                self.assertEqual(store.count("codex_stream_responses"), 1)
                store.connection.execute("DELETE FROM codex_stream_requests WHERE request_id = ?", ("request-1",))
                store.connection.commit()
                self.assertEqual(store.count("codex_stream_requests"), 0)
                self.assertEqual(store.count("codex_stream_attempts"), 0)
                self.assertEqual(store.count("codex_stream_leases"), 0)
                self.assertEqual(store.count("codex_stream_artifacts"), 0)
                self.assertEqual(store.count("codex_stream_responses"), 0)


class CodexStreamWorkerFrontier2Tests(unittest.TestCase):
    def test_enqueue_request_creates_ready_request_row_and_row_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                payload = store.enqueue_request(
                    request_id="request-frontier2-a1",
                    app_id=42,
                    channel_uuid="2cf5cd633a76421c8c4c5a1b93eb033e",
                    requester_kind="user",
                    requester_id="alice",
                    task_type="health_check",
                    input_ref="payload://health-check-1",
                    evidence_refs=[{"type": "message", "id": "m-123"}],
                    model_budget_hint="spark_suitable",
                    priority="high",
                    deadline_ms=15000,
                    idempotency_key="idem-1",
                    status="ready",
                )
                self.assertEqual(payload["request_id"], "request-frontier2-a1")
                self.assertEqual(payload["attempt_count"], 0)
                request_row = store.connection.execute(
                    "SELECT * FROM codex_stream_requests WHERE request_id = ?",
                    ("request-frontier2-a1",),
                ).fetchone()
                self.assertIsNotNone(request_row)
                self.assertEqual(request_row["status"], "ready")
                self.assertEqual(request_row["requester_kind"], "user")
                self.assertEqual(request_row["task_type"], "health_check")
                self.assertEqual(request_row["model_budget_hint"], "spark_suitable")
                self.assertEqual(request_row["priority"], "high")
                self.assertEqual(request_row["deadline_ms"], 15000)
                self.assertEqual(request_row["idempotency_key"], "idem-1")

    def test_enqueue_request_idempotent_key_returns_existing_when_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                first = store.enqueue_request(
                    request_id="request-frontier2-b1",
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="user",
                    requester_id="alice",
                    task_type="health_check",
                    input_ref="payload://first",
                    idempotency_key="idem-2",
                )
                duplicate = store.enqueue_request(
                    request_id=None,
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="user",
                    requester_id="alice",
                    task_type="health_check",
                    input_ref="payload://first",
                    idempotency_key="idem-2",
                )
                self.assertEqual(first["request_id"], duplicate["request_id"])
                self.assertEqual(store.count("codex_stream_requests"), 1)

    def test_claim_next_request_creates_attempt_and_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                store.enqueue_request(
                    request_id="request-frontier2-c1",
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="system",
                    requester_id="svc-1",
                    task_type="sop_compile",
                    input_ref="payload://compile-1",
                )
                claim = store.claim_next_request(worker_id="worker-a")
                self.assertIsNotNone(claim)
                self.assertEqual(claim["request"]["request_id"], "request-frontier2-c1")
                self.assertEqual(claim["attempt_number"], 1)
                self.assertIsNotNone(claim["lease_id"])
                request_row = store.connection.execute(
                    "SELECT status, attempt_count, lease_id FROM codex_stream_requests WHERE request_id = ?",
                    ("request-frontier2-c1",),
                ).fetchone()
                self.assertEqual(request_row["status"], "claimed")
                self.assertEqual(request_row["attempt_count"], 1)
                self.assertEqual(request_row["lease_id"], claim["lease_id"])
                attempt_row = store.connection.execute(
                    "SELECT * FROM codex_stream_attempts WHERE request_id = ?",
                    ("request-frontier2-c1",),
                ).fetchone()
                self.assertEqual(attempt_row["status"], "running")
                self.assertEqual(attempt_row["worker_id"], "worker-a")
                lease_row = store.connection.execute(
                    "SELECT * FROM codex_stream_leases WHERE lease_id = ?",
                    (claim["lease_id"],),
                ).fetchone()
                self.assertEqual(lease_row["status"], "active")

    def test_heartbeat_lease_refreshes_ttl(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                store.enqueue_request(
                    request_id="request-frontier2-d1",
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="system",
                    requester_id="svc-1",
                    task_type="health_check",
                    input_ref="payload://heartbeat-1",
                )
                claim = store.claim_next_request(worker_id="worker-a")
                lease_id = claim["lease_id"]
                original_lease = store.connection.execute(
                    "SELECT heartbeat_at, expires_at FROM codex_stream_leases WHERE lease_id = ?",
                    (lease_id,),
                ).fetchone()
                time.sleep(0.01)
                refreshed = store.heartbeat_lease(lease_id=lease_id, lease_ttl_ms=120000)
                self.assertIsNotNone(refreshed)
                self.assertNotEqual(refreshed["heartbeat_at"], original_lease["heartbeat_at"])
                self.assertGreater(refreshed["expires_at"], original_lease["expires_at"])
                request_row = store.connection.execute(
                    "SELECT status FROM codex_stream_requests WHERE request_id = ?",
                    ("request-frontier2-d1",),
                ).fetchone()
                self.assertEqual(request_row["status"], "claimed")

    def test_complete_request_writes_response_and_marks_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                store.enqueue_request(
                    request_id="request-frontier2-e1",
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="system",
                    requester_id="svc-2",
                    task_type="health_check",
                    input_ref="payload://complete-1",
                )
                claim = store.claim_next_request(worker_id="worker-a")
                store.complete_request(
                    request_id="request-frontier2-e1",
                    attempt_id=claim["attempt_id"],
                    result_summary="ok",
                    result_ref="result://42",
                    model_route={"model": "gpt-5.5-codex", "spark_suitable": False},
                    justification_ref="just://1",
                    artifact_refs=("artifact://a1", "artifact://a2"),
                )
                request_row = store.connection.execute(
                    "SELECT status, completed_at, lease_id FROM codex_stream_requests WHERE request_id = ?",
                    ("request-frontier2-e1",),
                ).fetchone()
                self.assertEqual(request_row["status"], "completed")
                self.assertIsNotNone(request_row["completed_at"])
                self.assertIsNone(request_row["lease_id"])
                response_row = store.connection.execute(
                    "SELECT status, result_ref, justification_ref FROM codex_stream_responses WHERE request_id = ?",
                    ("request-frontier2-e1",),
                ).fetchone()
                self.assertEqual(response_row["status"], "completed")
                self.assertEqual(response_row["result_ref"], "result://42")
                self.assertEqual(response_row["justification_ref"], "just://1")

    def test_fail_request_can_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                store.enqueue_request(
                    request_id="request-frontier2-f1",
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="system",
                    requester_id="svc-3",
                    task_type="health_check",
                    input_ref="payload://fail-1",
                )
                claim = store.claim_next_request(worker_id="worker-a")
                fail = store.fail_request(
                    request_id="request-frontier2-f1",
                    attempt_id=claim["attempt_id"],
                    error_ref="err://bad",
                    retryable=True,
                    result_summary="transient",
                )
                self.assertEqual(fail["final_status"], "ready")
                request_row = store.connection.execute(
                    "SELECT status, error_ref FROM codex_stream_requests WHERE request_id = ?",
                    ("request-frontier2-f1",),
                ).fetchone()
                self.assertEqual(request_row["status"], "ready")
                self.assertEqual(request_row["error_ref"], "err://bad")
                attempt_row = store.connection.execute(
                    "SELECT status FROM codex_stream_attempts WHERE attempt_id = ?",
                    (claim["attempt_id"],),
                ).fetchone()
                self.assertIn(attempt_row["status"], {"failed", "needs_human", "blocked"})
                second_claim = store.claim_next_request(worker_id="worker-a")
                self.assertIsNotNone(second_claim)
                self.assertEqual(second_claim["request"]["request_id"], "request-frontier2-f1")
                self.assertEqual(second_claim["attempt_number"], 2)

    def test_fail_request_nonretryable_updates_terminal_status(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                store.enqueue_request(
                    request_id="request-frontier2-g1",
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="system",
                    requester_id="svc-4",
                    task_type="health_check",
                    input_ref="payload://fail-2",
                )
                claim = store.claim_next_request(worker_id="worker-a")
                fail = store.fail_request(
                    request_id="request-frontier2-g1",
                    attempt_id=claim["attempt_id"],
                    error_ref="err://critical",
                    retryable=False,
                    result_summary="blocked",
                    status="blocked",
                )
                self.assertEqual(fail["status"], "failed")
                self.assertEqual(fail["final_status"], "blocked")
                request_row = store.connection.execute(
                    "SELECT status, completed_at, error_ref FROM codex_stream_requests WHERE request_id = ?",
                    ("request-frontier2-g1",),
                ).fetchone()
                self.assertEqual(request_row["status"], "blocked")
                self.assertIsNotNone(request_row["completed_at"])
                self.assertEqual(request_row["error_ref"], "err://critical")

    def test_cancel_request_marks_cancelled_and_records_response(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                store.enqueue_request(
                    request_id="request-frontier2-h1",
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="admin",
                    requester_id="ops",
                    task_type="route_decision",
                    input_ref="payload://route",
                )
                cancelled = store.cancel_request("request-frontier2-h1", reason="obsolete")
                self.assertEqual(cancelled["status"], "cancelled")
                request_row = store.connection.execute(
                    "SELECT status, error_ref FROM codex_stream_requests WHERE request_id = ?",
                    ("request-frontier2-h1",),
                ).fetchone()
                self.assertEqual(request_row["status"], "cancelled")
                self.assertEqual(request_row["error_ref"], "obsolete")
                response_row = store.connection.execute(
                    "SELECT status, error_ref FROM codex_stream_responses WHERE request_id = ?",
                    ("request-frontier2-h1",),
                ).fetchone()
                self.assertEqual(response_row["status"], "cancelled")
                self.assertEqual(response_row["error_ref"], "obsolete")

    def test_request_status_aggregates_request_attempts_lease_and_responses(self):
        with tempfile.TemporaryDirectory() as temp:
            with CodexStreamWorkerStore(Path(temp) / "codex_stream.sqlite3") as store:
                store.enqueue_request(
                    request_id="request-frontier2-i1",
                    app_id=42,
                    channel_uuid=None,
                    requester_kind="user",
                    requester_id="alice",
                    task_type="health_check",
                    input_ref="payload://status-1",
                )
                claim = store.claim_next_request(worker_id="worker-a")
                store.complete_request(
                    request_id="request-frontier2-i1",
                    attempt_id=claim["attempt_id"],
                    result_summary="done",
                )
                snapshot = store.request_status("request-frontier2-i1")
                self.assertEqual(snapshot["request"]["request_id"], "request-frontier2-i1")
                self.assertEqual(snapshot["request"]["status"], "completed")
                self.assertEqual(len(snapshot["attempts"]), 1)
                self.assertIsNone(snapshot["lease"])
                self.assertEqual(len(snapshot["responses"]), 1)
                self.assertEqual(snapshot["responses"][0]["status"], "completed")
                self.assertEqual(snapshot["responses"][0]["result_summary"], "done")


if __name__ == "__main__":
    unittest.main()
