"""Explainer: LLM-judge rubric (faithfulness/coverage/clarity) + deterministic hallucination check.

Judges the explanations produced during `eval_generation` (rag variant), using the
generated config + requirements + validator result as the reference.
"""

from __future__ import annotations

import re
from typing import Any

from evals.common import judge, load_results, run_resumable
from infra_assistant.nodes import validation_summary

NAME = "explainer"
_RES_TYPE = re.compile(r"\baws_[a-z0-9_]+\b")


def hallucinated_resources(explanation: str, code: str) -> list[str]:
    """`aws_*` resource types named in the explanation that do not exist in the config."""

    text = explanation.split("```hcl")[0]
    return sorted({t for t in _RES_TYPE.findall(text) if t not in code and t != "aws_region"})


async def run(ctx: Any) -> list[dict[str, Any]]:
    items = [r for r in load_results("generation") if r["variant"] == "rag" and r["explanation"]]

    async def one(item: dict[str, Any]) -> dict[str, Any]:
        explanation = item["explanation"].split("**Validator:**")[0].strip()
        reference = (f"Requirements: {item['requirements']}\n\nValidator result:\n"
                     f"{validation_summary(item['validation_summary'])}\n\nTerraform config:\n{item['code']}")
        score = await judge(ctx.models, "Explain this Terraform configuration to the user, mapping each requirement "
                                        "to concrete resources, with an honest validation summary and cost estimate.",
                            reference, explanation)
        halluc = hallucinated_resources(explanation, item["code"])
        return {**score, "hallucinated_resources": halluc, "ok": score["faithfulness"] >= 4 and not halluc}

    return await run_resumable(NAME, items, one)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    avg = lambda k: round(sum(r[k] for r in rows) / len(rows), 2)  # noqa: E731
    return {
        "n": len(rows),
        "faithfulness": avg("faithfulness"), "coverage": avg("coverage"), "clarity": avg("clarity"),
        "share_faithfulness>=4": round(sum(r["faithfulness"] >= 4 for r in rows) / len(rows), 3),
        "explanations_with_hallucinated_resources": sum(bool(r["hallucinated_resources"]) for r in rows),
        "lowest": sorted(({"id": r["id"], "faithfulness": r["faithfulness"], "rationale": r["rationale"]} for r in rows),
                         key=lambda x: x["faithfulness"])[:3],
    }
