from __future__ import annotations

from typing import Any


def _supported_task_types() -> dict[str, str]:
    return {
        "sop_compile": "SOP compile is routed to manual review in the prototype boundary.",
        "route_decision": "Routing decision requires external policy hooks and is not auto-executed.",
        "llm_decision": "LLM decision is intentionally unimplemented pending live model handoff.",
        "history_context_pack": "History context pack preparation is currently delegated to upstream runtime.",
        "spec_explosion": "Spec explosion is staged for dedicated processor integration.",
        "justification_record": "Justification recording is a manual/provenance control-plane concern.",
        "tool_handoff": "Tool handoff requires authenticated external handoff target.",
        "health_check": "health_check is fully handled in the streaming frontier.",
    }


def execute_stubbed_task(request: dict[str, Any], *, fallback_status: str = "needs_human") -> dict[str, Any]:
    task_type = str(request.get("task_type", ""))
    if task_type == "health_check":
        return {
            "status": "completed",
            "result_summary": "health check stub executed",
            "result_ref": None,
            "artifact_refs": [],
            "retryable": False,
        }
    if task_type in _supported_task_types():
        return {
            "status": fallback_status,
            "result_summary": _supported_task_types()[task_type],
            "error_ref": f"stubbed_task:{task_type}",
            "retryable": False,
            "artifact_refs": [],
        }
    return {
        "status": "needs_human",
        "result_summary": f"unsupported stub task_type: {task_type}",
        "error_ref": "stubbed_task:unsupported",
        "retryable": False,
        "artifact_refs": [],
    }
