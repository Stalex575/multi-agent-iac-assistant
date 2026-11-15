"""Retriever: Hit@k, Recall@k, MRR, nDCG@5 for BM25 vs dense vs hybrid (RRF).

Relevance is labelled at document level (any chunk of a relevant doc counts).
BM25 runs fully offline; dense/hybrid need the Gemini embedding index.
"""

from __future__ import annotations

import math
from typing import Any

from evals.common import load_dataset, run_resumable

NAME = "retrieval"
MODES = ("bm25", "dense", "hybrid")
K = 10


def doc_ranking(hits: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for h in hits:
        if h["doc_id"] not in seen:
            seen.append(h["doc_id"])
    return seen


def scores(ranking: list[str], relevant: list[str]) -> dict[str, float]:
    rel = set(relevant)
    first = next((i for i, d in enumerate(ranking) if d in rel), None)
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(ranking[:5]) if d in rel)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(rel), 5)))
    return {
        "hit@1": float(first == 0),
        "hit@5": float(first is not None and first < 5),
        "recall@5": len(rel & set(ranking[:5])) / len(rel),
        "recall@10": len(rel & set(ranking[:10])) / len(rel),
        "mrr": 1 / (first + 1) if first is not None else 0.0,
        "ndcg@5": dcg / idcg if idcg else 0.0,
    }


async def run(ctx: Any, modes: tuple[str, ...] = MODES) -> list[dict[str, Any]]:
    retriever = ctx.retriever

    async def one(item: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {"query": item["query"], "relevant": item["relevant"]}
        for mode in modes:
            # pull a deep chunk list so 10 distinct documents survive de-duplication
            ranking = doc_ranking(await retriever.search(item["query"], mode=mode, k=40))[:K]
            row[mode] = {"ranking": ranking, **scores(ranking, item["relevant"])}
        row["ok"] = bool(row[modes[-1]]["hit@5"])
        return row

    name = NAME if len(modes) > 1 else f"{NAME}_{modes[0]}"  # offline BM25-only run gets its own file
    return await run_resumable(name, load_dataset(NAME), one)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    out: dict[str, Any] = {"n": len(rows)}
    for mode in MODES:
        mode_rows = [r for r in rows if mode in r]
        if not mode_rows:
            continue
        metrics = mode_rows[0][mode].keys() - {"ranking"}
        out[mode] = {m: round(sum(r[mode][m] for r in mode_rows) / len(mode_rows), 3) for m in sorted(metrics)}
        for prefix, label in (("t", "terraform_docs"), ("w", "well_architected"), ("p", "paraphrased")):
            sub = [r for r in mode_rows if r["id"].startswith(prefix)]
            if sub:
                out[mode][f"hit@5_{label}"] = round(sum(r[mode]["hit@5"] for r in sub) / len(sub), 3)
    best = "hybrid" if "hybrid" in out else "bm25"
    out["misses_" + best] = [{"id": r["id"], "query": r["query"], "relevant": r["relevant"], "top3": r[best]["ranking"][:3]}
                             for r in rows if best in r and not r[best]["hit@5"]]
    return out
