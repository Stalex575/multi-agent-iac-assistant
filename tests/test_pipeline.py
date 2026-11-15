"""End-to-end graph tests with fake LLMs (real terraform validate, real BM25/cosine)."""

from __future__ import annotations

from typing import Any

import pytest

from infra_assistant.graph import build_app, route_after_validator
from infra_assistant.schemas import CollectorResult, InfraRequirements, IntentResult, TerraformDraft
from infra_assistant.state import turn_input
from tests.conftest import VALID_HCL, fake_bundle

FULL_REQ = InfraRequirements(workload="Flask API", traffic="50 rps", network_access="public_frontend_private_backend",
                             availability="multi_az", database="postgres", monthly_budget_usd=300)


def collector_two_turns(messages: list[Any], schema: type | None) -> CollectorResult:
    convo = messages[-1][1]
    if "300" in convo:  # second user turn answered the follow-up
        return CollectorResult(requirements=FULL_REQ, follow_up_question="")
    return CollectorResult(requirements=InfraRequirements(workload="Flask API", database="postgres"),
                           follow_up_question="What traffic, availability, network access and budget?")


def router(intent: str):
    return lambda m, s: IntentResult(intent=intent, reason="test")


async def run(app: Any, message: str, thread: str = "t1") -> dict[str, Any]:
    return await app.ainvoke(turn_input(message), config={"configurable": {"thread_id": thread}})


async def test_multi_turn_collection_then_valid_terraform(settings) -> None:
    generated: list[str] = []

    def generator(messages: list[Any], schema: type | None) -> TerraformDraft:
        broken = not generated  # first attempt invalid -> exercises the self-correction loop
        generated.append("x")
        hcl = VALID_HCL.replace("storage_encrypted           = true", "bogus_argument = 1") if broken else VALID_HCL
        return TerraformDraft(hcl=hcl, design_notes="- notes")

    models = fake_bundle(router=router("TERRAFORM_REQUEST"), collector=collector_two_turns,
                         generator=generator, writer=lambda m, s: "Architecture: explained.")
    app = build_app(settings, models=models, use_cache=False)

    first = await run(app, "I need infra for my Flask API with postgres")
    assert first["awaiting_requirements"] is True
    assert "budget" in first["final_response"]
    assert [s["node"] for s in first["trace"]] == ["intent_router", "requirements_collector"]

    second = await run(app, "50 rps, multi-AZ, public API, $300 a month")
    nodes = [s["node"] for s in second["trace"]]
    assert nodes[0] == "intent_router" and second["trace"][0].get("skipped_llm")
    assert nodes.count("terraform_generator") == 2  # one fix iteration
    assert second["validation"]["passed"] is True
    assert "```hcl" in second["final_response"]
    assert len(second["history"]) == 4  # user, assistant, user, assistant


async def test_out_of_scope(settings) -> None:
    app = build_app(settings, models=fake_bundle(router=router("OUT_OF_SCOPE")), use_cache=False)
    result = await run(app, "give me a lasagna recipe")
    assert "outside my scope" in result["final_response"]


async def test_question_path_uses_cache_on_repeat(settings) -> None:
    models = fake_bundle(router=router("INFRA_QUESTION"), writer=lambda m, s: "Use multi-AZ [1].")
    app = build_app(settings, models=models, use_cache=True)
    first = await run(app, "how do I make postgres highly available", "q1")
    assert first["cache_hit"] is False and "Sources" in first["final_response"]
    second = await run(app, "how do I make postgres highly available", "q2")
    assert second["cache_hit"] is True


@pytest.mark.parametrize("passed,attempts,expected", [(True, 0, "explain"), (False, 0, "fix"), (False, 2, "explain")])
def test_route_after_validator(passed: bool, attempts: int, expected: str) -> None:
    state = {"validation": {"passed": passed}, "fix_attempts": attempts}
    assert route_after_validator(state, max_fix_retries=2) == expected
