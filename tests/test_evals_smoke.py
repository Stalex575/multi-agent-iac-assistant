"""Run every LLM eval once with fake models so a real (slow, rate-limited) run cannot crash on a bug."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from evals import common, eval_advisor, eval_cache, eval_collector, eval_explainer, eval_generation, eval_router
from infra_assistant.nodes import Nodes
from infra_assistant.schemas import CollectorResult, InfraRequirements, IntentResult, JudgeScore, TerraformDraft
from infra_assistant.store import KnowledgeIndex, Retriever
from tests.conftest import VALID_HCL, fake_bundle


def responder(messages: list[Any], schema: type | None) -> Any:
    if schema is IntentResult:
        return IntentResult(intent="INFRA_QUESTION", reason="fake")
    if schema is CollectorResult:
        return CollectorResult(requirements=InfraRequirements(workload="api", database="postgres"), follow_up_question="more?")
    if schema is TerraformDraft:
        return TerraformDraft(hcl=VALID_HCL, design_notes="- n")
    if schema is JudgeScore:
        return JudgeScore(faithfulness=4, coverage=4, clarity=5, rationale="fine")
    return "Answer with a citation [1] and aws_db_instance."


@pytest.fixture
def ctx(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(common, "RESULTS", tmp_path / "results")
    models = fake_bundle(router=responder, collector=responder, generator=responder, writer=responder, judge=responder)
    retriever = Retriever(KnowledgeIndex.load(settings.index_dir), models.embeddings, 3)
    nodes = Nodes(settings=settings, models=models, retriever=retriever, cache=None)
    return SimpleNamespace(settings=settings, models=models, retriever=retriever, nodes=nodes)


async def test_all_llm_evals_run_and_summarize(ctx) -> None:
    for module in (eval_router, eval_collector, eval_cache, eval_generation, eval_explainer, eval_advisor):
        rows = await module.run(ctx)
        assert rows, module.NAME
        assert module.summarize(rows), module.NAME
    gen = eval_generation.summarize(common.load_results("generation"))
    assert gen["rag"]["validate_pass@final"] == 1.0
