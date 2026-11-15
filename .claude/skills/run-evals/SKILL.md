---
name: run-evals
description: Run the per-component evaluation suite for InfraPilot, interpret the scores, and update the report. Use when asked to evaluate, benchmark, measure quality, or after changing a prompt/node.
---

# Run evals

1. Preconditions: `.env` exists with `GOOGLE_API_KEY` (never print it), index built
   (`data/index/meta.jsonl` exists; otherwise run `python scripts/build_index.py`).
2. Run everything (resumable — safe to re-run after a network drop):
   `python -m evals.run_all` or a single one: `python -m evals.run_all --only router`.
   To force a fresh run of one eval delete `evals/results/<name>.jsonl`.
3. Render: `python -m evals.make_report` → `docs/eval_report.md` (+ summary JSON).
4. Interpret, do not just paste numbers. For each component write in the report:
   - is the score high or low relative to the baseline row in the table,
   - the 2–3 dominant failure cases (look at the `*.jsonl` rows with `ok=false`),
   - one concrete improvement (prompt change, more data, threshold change).
5. Log the run in `docs/crisp_dm_log.md` under **Evaluation** with date and model names.

Metric cheat-sheet used in this repo:
| Component | Metric |
|---|---|
| Intent router | accuracy, macro-F1, confusion matrix |
| Requirements collector | slot precision/recall/F1, follow-up correctness, avg turns |
| Retriever | Recall@k, MRR, nDCG@5 for BM25 vs dense vs hybrid |
| Semantic cache | precision/recall/F1 of hit decision across thresholds |
| Generator + validator | validate pass@1 / pass@final, policy pass, requirement coverage |
| Validator itself | detection accuracy on labelled good/bad HCL |
| Explainer / advisor | LLM-judge rubric 1–5 (faithfulness, coverage, clarity) + citation validity |
