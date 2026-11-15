"""Requirements Collector: slot accuracy, hallucinated slots, missing-slot detection, turns.

Each gold conversation is replayed turn by turn through the real collector node; the
assistant's follow-up question is appended to history exactly as in production.
"""

from __future__ import annotations

from typing import Any

from evals.common import base_state, load_dataset, prf, run_resumable

NAME = "collector"
PUBLIC_EQUIV = {"public", "public_frontend_private_backend"}


def slot_match(slot: str, gold: Any, pred: Any, lenient: bool) -> bool:
    if gold == "*":
        return pred not in (None, "")
    if lenient and slot == "network_access" and {gold, pred} <= PUBLIC_EQUIV:
        return True
    return pred == gold


async def run(ctx: Any) -> list[dict[str, Any]]:
    async def one(item: dict[str, Any]) -> dict[str, Any]:
        state = base_state()
        state.update(requirements={}, followup_turns=0, awaiting_requirements=False)
        first_missing: list[str] | None = None
        questions: list[str] = []
        extracted: dict[str, Any] = {}
        completed_turn = None
        for turn_no, message in enumerate(item["turns"], 1):
            state["history"] = state["history"] + [{"role": "user", "content": message}]
            state["user_message"] = message
            out = await ctx.nodes.requirements_collector(state)
            info = out["trace"][-1]
            if first_missing is None:
                first_missing = info["missing"]
            assumed = set(info.get("assumed", []))
            extracted = {k: (None if k in assumed else v) for k, v in out["requirements"].items()}
            state.update(requirements=extracted if out.get("awaiting_requirements") else out["requirements"],
                         followup_turns=out.get("followup_turns", 0),
                         awaiting_requirements=out.get("awaiting_requirements", False))
            if out.get("awaiting_requirements"):
                questions.append(out["final_response"])
                state["history"] = state["history"] + [{"role": "assistant", "content": out["final_response"]}]
            elif completed_turn is None:
                completed_turn = turn_no
                state["requirements"] = extracted  # keep collecting if more scripted turns follow
        gold = item["gold"]
        strict = {s: slot_match(s, g, extracted.get(s), False) for s, g in gold.items()}
        lenient = {s: slot_match(s, g, extracted.get(s), True) for s, g in gold.items()}
        return {"gold": gold, "extracted": extracted, "strict": strict, "lenient": lenient,
                "first_missing_pred": first_missing or [], "first_missing_gold": item["missing_after_first"],
                "questions": questions, "completed_turn": completed_turn, "n_turns": len(item["turns"]),
                "ok": all(lenient.values())}

    return await run_resumable(NAME, load_dataset(NAME), one)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    slots: dict[str, list[bool]] = {}
    for r in rows:
        for s, ok in r["lenient"].items():
            slots.setdefault(s, []).append(ok)
    total = sum(len(r["strict"]) for r in rows)
    tp = fp = fn = 0
    hallucinated = 0
    for r in rows:
        pred, gold = set(r["first_missing_pred"]), set(r["first_missing_gold"])
        tp, fp, fn = tp + len(pred & gold), fp + len(pred - gold), fn + len(gold - pred)
        hallucinated += len(gold - pred)  # slot user had NOT given, but collector filled it at turn 1
    return {
        "n_conversations": len(rows),
        "slot_accuracy_strict": round(sum(sum(r["strict"].values()) for r in rows) / total, 3),
        "slot_accuracy_lenient": round(sum(sum(r["lenient"].values()) for r in rows) / total, 3),
        "per_slot_accuracy": {s: round(sum(v) / len(v), 3) for s, v in slots.items()},
        "missing_slot_detection_turn1": prf(tp, fp, fn),
        "hallucinated_slots_turn1": hallucinated,
        "conversations_fully_correct": round(sum(r["ok"] for r in rows) / len(rows), 3),
        "avg_followup_questions": round(sum(len(r["questions"]) for r in rows) / len(rows), 2),
        "errors": [{"id": r["id"], "wrong": {s: {"gold": r["gold"][s], "pred": r["extracted"].get(s)}
                                             for s, ok in r["lenient"].items() if not ok}}
                   for r in rows if not r["ok"]],
    }
