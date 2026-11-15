# CLAUDE.md - instructions for the AI coding agent

This file is loaded into the coding agent's context on every session. It is the
single source of truth for *how* the agent works on this repo. `AGENTS.md` points here
so other agents (Codex, Cursor, Gemini CLI) get the same rules.

## Project in one paragraph

**InfraPilot** - an NLP system that turns a natural-language description of a small
AWS workload into a **validated Terraform configuration** plus an explanation.
LangGraph pipeline: Intent Router → Requirements Collector (multi-turn slot filling)
→ Hybrid Retriever (BM25 + Gemini embeddings over AWS Well-Architected + Terraform
provider docs) → Terraform Generator → Validator (`terraform fmt`, `terraform validate`,
static policy + requirement checks; failures loop back to the generator) → Explainer.
A second path answers general infra questions (semantic cache → retriever → advisor
with citations). Built on top of the author's earlier `multi-agent-triage` project.

## CRISP-DM is mandatory

Every piece of work is tagged with the CRISP-DM phase it belongs to and logged in
`docs/crisp_dm_log.md` (use the `crisp-dm-log` skill). Work in this order and never
skip a phase:

1. **Business Understanding** - requirement in scope? success metric defined? If a
   feature has no measurable success criterion, define one *before* writing code.
2. **Data Understanding** - inspect real data before generating synthetic data.
   Prefer public sources (AWS docs, Terraform provider docs). Record counts/sizes.
3. **Data Preparation** - scripts in `scripts/`, deterministic, idempotent, resumable.
   Never hand-edit files in `data/corpus/` or `data/index/`.
4. **Modeling** - graph nodes in `src/infra_assistant/`. One node = one responsibility.
5. **Evaluation** - every component has an eval in `evals/` with a metric that can
   distinguish good from bad output. No component ships without one.
6. **Deployment** - Gradio UI (`infra_assistant.ui`) + CLI; README run instructions.

## Hard rules

- **Secrets:** never open, print, or log `.env` or API keys. Only `.env.example` is
  edited. Missing keys are diagnosed via `Settings` error messages.
- **Single LLM provider:** Google Gemini via `langchain-google-genai`. Model names come
  from `Settings` (env), never hard-coded in nodes.
- **Structured outputs:** every LLM call that drives control flow uses
  `with_structured_output(<pydantic model>)`. Free-text LLM output is only allowed for
  user-facing prose (advisor answer, explanation) and HCL.
- **Determinism where possible:** if a check can be done with code (HCL parsing,
  terraform CLI, regex), do it with code, not an LLM.
- **Generated Terraform** never contains `terraform {}` or `provider {}` blocks - those
  live in `terraform_sandbox/versions.tf` so validation is reproducible offline.
- **Tests:** `pytest` must pass offline (no network, no keys). LLM calls are faked
  in tests via the `FakeModels` fixtures in `tests/conftest.py`.
- **Evals are resumable:** each eval appends one JSON line per item to
  `evals/results/<name>.jsonl` and skips items already present (flaky network).

## Code conventions

- Python 3.12, type hints everywhere, `from __future__ import annotations`.
- Pydantic v2 models for all schemas (`schemas.py`). LangGraph state is a `TypedDict`
  (`state.py`).
- Prompts live only in `prompts.py`. Nodes live only in `nodes.py`. Graph wiring only
  in `graph.py`.
- Functions < ~50 lines; no dead code; no commented-out code; docstrings explain *why*.
- New graph node → follow the `add-graph-node` skill (node + wiring + test + eval).

## Commands

```bash
source .venv/bin/activate
pytest -q                                  # offline tests
python scripts/fetch_docs.py               # data understanding (download docs)
python scripts/build_index.py              # data preparation (chunk + embed)
python -m evals.run_all                    # evaluation (needs GOOGLE_API_KEY)
python -m evals.make_report                # renders evals/results -> docs/eval_report.md
python -m infra_assistant.ui               # Gradio demo on :7860
```

## Definition of done for any task

1. Code + offline test pass. 2. Eval exists/updated for the touched component.
3. `docs/crisp_dm_log.md` has an entry. 4. README updated if behaviour changed.
