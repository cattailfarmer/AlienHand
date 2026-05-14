from __future__ import annotations

from dataclasses import dataclass


SPARK_TASKS = frozenset(
    {
        "health_check",
        "history_context_pack",
    },
)


HIGH_REASONING_TASKS = frozenset(
    {
        "llm_decision",
        "route_decision",
        "sop_compile",
        "spec_explosion",
        "justification_record",
        "tool_handoff",
    },
)


MODEL_SPARK = "gpt-5.3-codex-spark"
MODEL_HIGH = "gpt-5.5-codex"


@dataclass(frozen=True)
class ModelRoute:
    selected_model: str
    budget_class: str
    reason: str


def route_codex_stream_request(
    *,
    task_type: str,
    model_budget_hint: str = "unknown",
) -> ModelRoute:
    if not task_type:
        raise ValueError("task_type is required for routing")
    normalized_hint = (model_budget_hint or "unknown").strip().lower()
    if normalized_hint == "spark_suitable":
        return ModelRoute(
            selected_model=MODEL_SPARK,
            budget_class="spark_suitable",
            reason="explicit spark_suitable hint",
        )
    if normalized_hint == "high_reasoning":
        return ModelRoute(
            selected_model=MODEL_HIGH,
            budget_class="high_reasoning",
            reason="explicit high_reasoning hint",
        )
    if normalized_hint.startswith("explicit_model:"):
        return ModelRoute(
            selected_model=normalized_hint.split(":", 1)[1],
            budget_class="explicit_model",
            reason="explicit model override hint",
        )
    if task_type in HIGH_REASONING_TASKS:
        return ModelRoute(
            selected_model=MODEL_HIGH,
            budget_class="high_reasoning",
            reason=f"task_type {task_type} requires high reasoning",
        )
    if task_type in SPARK_TASKS:
        return ModelRoute(
            selected_model=MODEL_SPARK,
            budget_class="spark_suitable",
            reason=f"task_type {task_type} is operational or non-creative",
        )
    return ModelRoute(
        selected_model=MODEL_HIGH,
        budget_class="high_reasoning",
        reason=f"task_type {task_type} defaults to high reasoning for safety",
    )
