"""LangGraph state. Persisted per conversation thread by the checkpointer."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import NotRequired, TypedDict


class AssistantState(TypedDict):
    # --- conversation (persists across turns) ---
    user_message: str
    history: Annotated[list[dict[str, str]], operator.add]  # [{"role","content"}], appended
    requirements: NotRequired[dict[str, Any]]  # InfraRequirements.model_dump()
    awaiting_requirements: NotRequired[bool]  # True while collector waits for answers
    followup_turns: NotRequired[int]

    # --- per-turn working memory (reset by `start_turn`) ---
    intent: NotRequired[str]
    intent_reason: NotRequired[str]
    assumed_slots: NotRequired[list[str]]
    cache_hit: NotRequired[bool]
    cache_score: NotRequired[float]
    retrieved: NotRequired[list[dict[str, Any]]]  # [{"id","title","source","text","score"}]
    terraform_code: NotRequired[str]
    design_notes: NotRequired[str]
    validation: NotRequired[dict[str, Any]]  # ValidationReport.as_dict()
    fix_attempts: NotRequired[int]
    final_response: NotRequired[str]
    trace: NotRequired[list[dict[str, Any]]]  # per-turn node log shown in the UI


TURN_RESET: dict[str, Any] = {
    "intent": "",
    "intent_reason": "",
    "assumed_slots": [],
    "cache_hit": False,
    "cache_score": 0.0,
    "retrieved": [],
    "terraform_code": "",
    "design_notes": "",
    "validation": {},
    "fix_attempts": 0,
    "final_response": "",
    "trace": [],
}


def turn_input(message: str) -> dict[str, Any]:
    """Input for one user turn; per-turn keys are reset, conversation keys persist."""

    cleaned = message.strip()
    if not cleaned:
        raise ValueError("message must not be empty")
    return {"user_message": cleaned, "history": [{"role": "user", "content": cleaned}], **TURN_RESET}
