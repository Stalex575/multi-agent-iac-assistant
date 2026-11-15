"""Generator + Validator loop (end-to-end for the Terraform path, given complete requirements).

Metrics: validate pass@1 (first draft) vs pass@final (after the self-correction loop),
policy-clean rate, requirement coverage, attempts, latency. Optional ablation
`no_rag` runs the same specs with retrieval disabled to measure what RAG adds.
The explanation produced at the end is stored for `eval_explainer`.
"""

from __future__ import annotations

from typing import Any

from evals.common import base_state, load_dataset, run_resumable

NAME = "generation"


def _attempt_summary(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "syntax_ok": v["syntax_ok"], "validate_ok": v["validate_ok"], "passed": v["passed"],
        "errors": v["errors"][:5],
        "high_policy": [f["rule"] for f in v["policy"] if f["severity"] == "HIGH"],
        "medium_policy": [f["rule"] for f in v["policy"] if f["severity"] != "HIGH"],
        "req_checks": len(v["requirements"]), "req_failed": [r["check"] for r in v["requirements"] if not r["ok"]],
    }


async def run(ctx: Any, ablation: bool = False) -> list[dict[str, Any]]:
    nodes, max_fix = ctx.nodes, ctx.settings.max_fix_retries
    items = load_dataset(NAME)
    if ablation:
        items = [{**it, "id": f"{it['id']}:no_rag", "no_rag": True} for it in items]

    async def one(item: dict[str, Any]) -> dict[str, Any]:
        state = base_state("generate", requirements=item["requirements"], assumed_slots=[])
        if not item.get("no_rag"):
            state.update(await nodes.retrieve_for_terraform(state))
        attempts = []
        while True:
            state.update(await nodes.terraform_generator(state))
            state.update(await nodes.validator(state))
            attempts.append(_attempt_summary(state["validation"]))
            if state["validation"]["passed"] or state["fix_attempts"] >= max_fix:
                break
        explanation = "" if item.get("no_rag") else (await nodes.explainer(state))["final_response"]
        final = attempts[-1]
        return {
            "variant": "no_rag" if item.get("no_rag") else "rag",
            "requirements": item["requirements"], "attempts": attempts, "n_attempts": len(attempts),
            "pass_at_1": attempts[0]["validate_ok"], "validate_final": final["validate_ok"],
            "policy_clean_final": not final["high_policy"],
            "req_coverage_final": 1 - len(final["req_failed"]) / final["req_checks"] if final["req_checks"] else 1.0,
            "passed_final": final["passed"], "ok": final["passed"],
            "retrieved_docs": [h["doc_id"] for h in state.get("retrieved", [])],
            "code": state["terraform_code"], "explanation": explanation,
            "validation_summary": state["validation"],
        }

    return await run_resumable(NAME, items, one)


def _rate(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(bool(r[key]) for r in rows) / len(rows), 3) if rows else 0.0


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for variant in ("rag", "no_rag"):
        sub = [r for r in rows if r["variant"] == variant]
        if not sub:
            continue
        out[variant] = {
            "n": len(sub),
            "validate_pass@1": _rate(sub, "pass_at_1"),
            "validate_pass@final": _rate(sub, "validate_final"),
            "policy_clean@1": round(sum(not r["attempts"][0]["high_policy"] for r in sub) / len(sub), 3),
            "policy_clean@final": _rate(sub, "policy_clean_final"),
            "requirement_coverage@final": round(sum(r["req_coverage_final"] for r in sub) / len(sub), 3),
            "all_checks_passed@1": round(sum(r["attempts"][0]["passed"] for r in sub) / len(sub), 3),
            "all_checks_passed@final": _rate(sub, "passed_final"),
            "avg_attempts": round(sum(r["n_attempts"] for r in sub) / len(sub), 2),
            "avg_latency_s": round(sum(r["latency_s"] for r in sub) / len(sub), 1),
            "medium_findings_final": sum(len(r["attempts"][-1]["medium_policy"]) for r in sub),
        }
    rag = [r for r in rows if r["variant"] == "rag"]
    out["failure_examples"] = [
        {"id": r["id"], "first_attempt_errors": r["attempts"][0]["errors"][:2],
         "first_high_policy": r["attempts"][0]["high_policy"], "first_req_failed": r["attempts"][0]["req_failed"],
         "final_passed": r["passed_final"]}
        for r in rag if not r["attempts"][0]["passed"]
    ]
    return out
