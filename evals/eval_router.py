"""Intent Router: accuracy, macro-F1, confusion matrix vs a keyword baseline."""

from __future__ import annotations

import re
from typing import Any

from evals.common import base_state, load_dataset, prf, run_resumable

NAME = "router"
LABELS = ["TERRAFORM_REQUEST", "INFRA_QUESTION", "OUT_OF_SCOPE"]
_TF = re.compile(r"\b(terraform|iac|deploy|host|set ?up|build|provision|spin up|infra(structure)?|create|migrate|add|make the|change the)\b", re.I)
_Q = re.compile(r"\b(aws|rds|s3|ec2|vpc|nat|alb|nlb|dynamodb|postgres|fargate|kubernetes|terraform|subnet|security group|autoscaling|backup|lambda|instance|ecs|well-architected|chaos|reliability|az)\b", re.I)


def keyword_baseline(text: str) -> str:
    """Rule baseline: shows how much the LLM router adds over simple keyword matching."""

    if _TF.search(text) and not text.strip().lower().startswith(("what", "how", "why", "when", "should", "is ", "explain", "difference")):
        return "TERRAFORM_REQUEST"
    if _Q.search(text):
        return "INFRA_QUESTION"
    return "OUT_OF_SCOPE"


async def run(ctx: Any) -> list[dict[str, Any]]:
    async def one(item: dict[str, Any]) -> dict[str, Any]:
        out = await ctx.nodes.intent_router(base_state(item["text"]))
        return {"label": item["label"], "pred": out["intent"], "reason": out["intent_reason"],
                "baseline": keyword_baseline(item["text"]), "ok": out["intent"] == item["label"], "text": item["text"]}

    return await run_resumable(NAME, load_dataset(NAME), one)


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    per_class = {}
    for lab in LABELS:
        tp = sum(r[key] == lab and r["label"] == lab for r in rows)
        fp = sum(r[key] == lab and r["label"] != lab for r in rows)
        fn = sum(r[key] != lab and r["label"] == lab for r in rows)
        per_class[lab] = prf(tp, fp, fn)
    return {
        "accuracy": round(sum(r[key] == r["label"] for r in rows) / len(rows), 3),
        "macro_f1": round(sum(c["f1"] for c in per_class.values()) / len(LABELS), 3),
        "per_class": per_class,
        "confusion": {g: {p: sum(r["label"] == g and r[key] == p for r in rows) for p in LABELS} for g in LABELS},
    }


def _subset_accuracy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return {"n": len(rows), "llm": round(sum(r["ok"] for r in rows) / len(rows), 3),
            "baseline": round(sum(r["baseline"] == r["label"] for r in rows) / len(rows), 3)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return {
        "n": len(rows),
        "llm": _metrics(rows, "pred"),
        "keyword_baseline": _metrics(rows, "baseline"),
        "accuracy_easy_vs_hard": {
            "easy": _subset_accuracy([r for r in rows if not r["id"].startswith("h")]),
            "hard": _subset_accuracy([r for r in rows if r["id"].startswith("h")]),
        },
        "errors": [{"id": r["id"], "text": r["text"], "gold": r["label"], "pred": r["pred"], "reason": r["reason"]}
                   for r in rows if not r["ok"]],
        "avg_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 2),
    }
