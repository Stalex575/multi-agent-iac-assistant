"""Advisor (question path): citation validity + LLM-judge groundedness against retrieved context."""

from __future__ import annotations

import re
from typing import Any

from evals.common import base_state, judge, load_dataset, run_resumable
from infra_assistant.store import format_context

NAME = "advisor"
_CITE = re.compile(r"\[(\d+)\]")


async def run(ctx: Any) -> list[dict[str, Any]]:
    questions = [r for r in load_dataset("router") if r["label"] == "INFRA_QUESTION"][:12]

    async def one(item: dict[str, Any]) -> dict[str, Any]:
        state = base_state(item["text"])
        state.update(await ctx.nodes.retrieve_for_question(state))
        out = await ctx.nodes.advisor(state)
        answer = out["final_response"].split("**Sources**")[0].strip()
        hits = state["retrieved"]
        cited = [int(c) for c in _CITE.findall(answer)]
        invalid = [c for c in cited if not 1 <= c <= len(hits)]
        score = await judge(ctx.models, f"Answer the question: {item['text']}", format_context(hits), answer)
        return {"question": item["text"], "answer": answer, "n_citations": len(cited), "invalid_citations": invalid,
                **score, "ok": score["faithfulness"] >= 4 and not invalid and bool(cited)}

    return await run_resumable(NAME, questions, one)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    avg = lambda k: round(sum(r[k] for r in rows) / len(rows), 2)  # noqa: E731
    return {
        "n": len(rows),
        "faithfulness": avg("faithfulness"), "coverage": avg("coverage"), "clarity": avg("clarity"),
        "answers_with_citation": round(sum(r["n_citations"] > 0 for r in rows) / len(rows), 3),
        "invalid_citation_rate": round(sum(len(r["invalid_citations"]) for r in rows) / max(1, sum(r["n_citations"] for r in rows)), 3),
        "lowest": sorted(({"id": r["id"], "q": r["question"], "faithfulness": r["faithfulness"], "rationale": r["rationale"]}
                          for r in rows), key=lambda x: x["faithfulness"])[:3],
    }
