"""Semantic cache: is "same question" detected by cosine similarity? Threshold sweep.

Positives are paraphrases; negatives are deliberately close but different questions
(same service, different setting) - the dangerous case where a cache hit returns a
wrong answer. We report precision/recall/F1 and the false-hit rate per threshold.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from evals.common import load_dataset, prf, run_resumable
from infra_assistant.store import similarity_text

NAME = "cache"
THRESHOLDS = [round(t, 2) for t in np.arange(0.80, 0.995, 0.01)]


async def run(ctx: Any) -> list[dict[str, Any]]:
    emb = ctx.models.embeddings

    async def one(item: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(1.5)  # 2 embeddings per pair; free tier allows 100 requests/min
        va, vb = await asyncio.gather(
            asyncio.to_thread(emb.embed_query, similarity_text(item["a"])),
            asyncio.to_thread(emb.embed_query, similarity_text(item["b"])),
        )
        a, b = np.asarray(va), np.asarray(vb)
        cos = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
        hit = cos >= ctx.settings.semantic_cache_threshold
        return {"a": item["a"], "b": item["b"], "same": item["same"], "cosine": round(cos, 4),
                "ok": hit == item["same"]}

    return await run_resumable(NAME, load_dataset("cache_pairs"), one)


def summarize(rows: list[dict[str, Any]], configured: float = 0.92) -> dict[str, Any]:
    if not rows:
        return {}
    sweep = []
    for t in THRESHOLDS:
        tp = sum(r["same"] and r["cosine"] >= t for r in rows)
        fp = sum(not r["same"] and r["cosine"] >= t for r in rows)
        fn = sum(r["same"] and r["cosine"] < t for r in rows)
        negatives = sum(not r["same"] for r in rows)
        sweep.append({"threshold": t, **prf(tp, fp, fn), "false_hit_rate": round(fp / negatives, 3)})
    best = max(sweep, key=lambda s: (s["f1"], s["threshold"]))
    safe = [s for s in sweep if s["false_hit_rate"] == 0.0]
    pos = [r["cosine"] for r in rows if r["same"]]
    neg = [r["cosine"] for r in rows if not r["same"]]
    return {
        "n_pairs": len(rows),
        "cosine_paraphrase_mean": round(float(np.mean(pos)), 3),
        "cosine_near_miss_mean": round(float(np.mean(neg)), 3),
        "max_near_miss_cosine": round(max(neg), 3),
        "at_configured": next((s for s in sweep if abs(s["threshold"] - configured) < 1e-9), None),
        "best_f1": best,
        "lowest_zero_false_hit": safe[0] if safe else None,
        "sweep": sweep,
        "hardest_negatives": sorted(({"a": r["a"], "b": r["b"], "cosine": r["cosine"]} for r in rows if not r["same"]),
                                    key=lambda x: -x["cosine"])[:3],
    }
