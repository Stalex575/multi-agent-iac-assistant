---
name: add-graph-node
description: Add or change a LangGraph node in InfraPilot (node + prompt + schema + wiring + offline test + eval). Use whenever a new pipeline component is introduced or an existing node's contract changes.
---

# Add a graph node

A node is only "done" when all six steps below are complete.

1. **Contract first.** Write down in `docs/crisp_dm_log.md` (Modeling): purpose, input
   state keys, output state keys, model call or tool call, and the success metric.
2. **Schema.** If the node's output drives control flow, add a Pydantic model to
   `src/infra_assistant/schemas.py` and call the LLM with `with_structured_output`.
3. **Prompt.** Add the system prompt + a `*_user_prompt()` builder to `prompts.py`.
   Prompts must state the output contract explicitly and include 1 short example
   when the format is non-obvious.
4. **Node.** Add an `async def` method to `Nodes` in `nodes.py`. It returns only the
   state keys it owns and appends a step to `trace` via `_trace(...)`.
5. **Wiring.** Register in `graph.py`; routing functions are pure functions of state
   and are unit-tested in `tests/test_graph_routing.py`.
6. **Test + eval.** Offline test with `FakeModels` (`tests/conftest.py`) and an eval
   module `evals/eval_<node>.py` registered in `evals/run_all.py`. Pick a metric that
   separates good from bad output (accuracy/F1 for classification, Recall@k/MRR for
   retrieval, pass-rate for validators, rubric judge for prose).

Finish with `pytest -q`.
