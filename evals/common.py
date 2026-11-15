"""Shared eval utilities: datasets, resumable result files, component wiring."""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "eval"
RESULTS = ROOT / "evals" / "results"


def load_dataset(name: str) -> list[dict[str, Any]]:
    path = DATA / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_results(name: str) -> list[dict[str, Any]]:
    path = RESULTS / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def run_resumable(
    name: str,
    items: list[dict[str, Any]],
    fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    key: str = "id",
) -> list[dict[str, Any]]:
    """Evaluate items one by one; rows already in results/<name>.jsonl are skipped.

    A failed item (network drop, quota) is logged and NOT written, so a re-run retries it.
    """

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.jsonl"
    done = {row[key] for row in load_results(name)}
    todo = [it for it in items if it[key] not in done]
    print(f"[{name}] {len(done)} done, {len(todo)} to run")
    for n, item in enumerate(todo, 1):
        start = time.perf_counter()
        try:
            row = await fn(item)
        except Exception as exc:
            print(f"[{name}] {item[key]} FAILED: {type(exc).__name__}: {str(exc)[:200]}")
            if "quota" in str(exc).lower() or "429" in str(exc):
                traceback.print_exc(limit=1)
            continue
        row.setdefault(key, item[key])
        row["latency_s"] = round(time.perf_counter() - start, 2)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{name}] {n}/{len(todo)} {item[key]} ok={row.get('ok', '-')}")
    return load_results(name)


@dataclass
class Context:
    settings: Any
    models: Any
    retriever: Any
    nodes: Any


def build_context() -> Context:
    from infra_assistant.config import Settings
    from infra_assistant.models import build_models
    from infra_assistant.nodes import Nodes
    from infra_assistant.store import KnowledgeIndex, Retriever

    settings = Settings.load()
    models = build_models(settings)
    retriever = Retriever(KnowledgeIndex.load(settings.index_dir), models.embeddings, settings.retrieval_top_k)
    nodes = Nodes(settings=settings, models=models, retriever=retriever, cache=None)
    return Context(settings, models, retriever, nodes)


def base_state(message: str = "", **extra: Any) -> dict[str, Any]:
    from infra_assistant.state import TURN_RESET

    return {"user_message": message, "history": [], **TURN_RESET, **extra}


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3)}


async def judge(models: Any, task: str, reference: str, response: str) -> dict[str, Any]:
    from infra_assistant import prompts
    from infra_assistant.schemas import JudgeScore

    llm = models.judge.with_structured_output(JudgeScore)
    score: JudgeScore = await llm.ainvoke(
        [("system", prompts.JUDGE_SYSTEM), ("human", prompts.judge_user(task, reference, response))]
    )
    return score.model_dump()
