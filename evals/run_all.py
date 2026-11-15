"""Run the evaluation suite.

  python -m evals.run_all                     # everything (needs GOOGLE_API_KEY)
  python -m evals.run_all --only router cache # selected evals
  python -m evals.run_all --offline           # validator + BM25 retrieval, no key needed
  python -m evals.run_all --only generation --ablation   # also run the no-RAG variant
"""

from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace

from evals import (eval_advisor, eval_cache, eval_collector, eval_explainer, eval_generation, eval_retrieval,
                   eval_router, eval_validator)

ORDER = ["validator", "retrieval", "router", "collector", "cache", "generation", "explainer", "advisor"]
MODULES = {"validator": eval_validator, "retrieval": eval_retrieval, "router": eval_router,
           "collector": eval_collector, "cache": eval_cache, "generation": eval_generation,
           "explainer": eval_explainer, "advisor": eval_advisor}


def offline_context() -> SimpleNamespace:
    from infra_assistant.config import PROJECT_ROOT
    from infra_assistant.store import KnowledgeIndex, Retriever

    return SimpleNamespace(retriever=Retriever(KnowledgeIndex.load(PROJECT_ROOT / "data" / "index"), embedder=None))


async def main_async(only: list[str], offline: bool, ablation: bool) -> None:
    if offline:
        ctx = offline_context()
        await eval_validator.run()
        await eval_retrieval.run(ctx, modes=("bm25",))
        return
    from evals.common import build_context

    ctx = build_context()
    for name in only or ORDER:
        print(f"\n===== {name} =====")
        if name == "generation":
            await eval_generation.run(ctx)
            if ablation:
                await eval_generation.run(ctx, ablation=True)
        else:
            await MODULES[name].run(ctx)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=[], choices=ORDER)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--ablation", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args.only, args.offline, args.ablation))


if __name__ == "__main__":
    main()
