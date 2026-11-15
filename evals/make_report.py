"""Render evals/results/*.jsonl into docs/eval_report.md (+ evals/results/summary.json)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from evals import (eval_advisor, eval_cache, eval_collector, eval_explainer, eval_generation, eval_retrieval,
                   eval_router, eval_validator)
from evals.common import RESULTS, ROOT, load_results


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def router_md(s: dict[str, Any]) -> str:
    llm, base = s["llm"], s["keyword_baseline"]
    out = [table(["", "Accuracy", "Macro-F1"], [["LLM router", llm["accuracy"], llm["macro_f1"]],
                                                 ["Keyword baseline", base["accuracy"], base["macro_f1"]]])]
    out.append("\nPer class (LLM):\n\n" + table(["Class", "P", "R", "F1"],
               [[c, m["precision"], m["recall"], m["f1"]] for c, m in llm["per_class"].items()]))
    labels = list(llm["confusion"])
    out.append("\nConfusion matrix (rows = gold, cols = predicted):\n\n" + table(
        ["gold \\ pred", *labels], [[g, *[llm["confusion"][g][p] for p in labels]] for g in labels]))
    if s["errors"]:
        out.append("\nErrors:\n" + "\n".join(f"- `{e['id']}` \"{e['text']}\" gold={e['gold']} pred={e['pred']}" for e in s["errors"]))
    return "\n".join(out)


def retrieval_md(s: dict[str, Any]) -> str:
    modes = [m for m in ("bm25", "dense", "hybrid") if m in s]
    metrics = ["hit@1", "hit@5", "recall@5", "mrr", "ndcg@5", "hit@5_terraform_docs", "hit@5_well_architected", "hit@5_paraphrased"]
    out = [table(["Metric", *modes], [[m, *[s[mode].get(m, "-") for mode in modes]] for m in metrics])]
    misses = next((v for k, v in s.items() if k.startswith("misses_")), [])
    if misses:
        out.append("\nMisses (best mode):\n" + "\n".join(f"- `{m['id']}` \"{m['query']}\" -> top3 {m['top3']}" for m in misses))
    return "\n".join(out)


def cache_md(s: dict[str, Any]) -> str:
    rows = [r for r in s["sweep"] if r["threshold"] in (0.85, 0.88, 0.9, 0.92, 0.94, 0.96)]
    out = [f"Mean cosine: paraphrases {s['cosine_paraphrase_mean']}, near-miss negatives {s['cosine_near_miss_mean']} "
           f"(max negative {s['max_near_miss_cosine']}).\n",
           table(["Threshold", "Precision", "Recall", "F1", "False-hit rate"],
                 [[r["threshold"], r["precision"], r["recall"], r["f1"], r["false_hit_rate"]] for r in rows]),
           f"\nBest F1: {s['best_f1']}  \nLowest threshold with zero false hits: {s['lowest_zero_false_hit']}",
           "\nHardest negatives:\n" + "\n".join(f"- {h['cosine']}: \"{h['a']}\" vs \"{h['b']}\"" for h in s["hardest_negatives"])]
    return "\n".join(out)


def generation_md(s: dict[str, Any]) -> str:
    variants = [v for v in ("rag", "no_rag") if v in s]
    keys = ["n", "validate_pass@1", "validate_pass@final", "policy_clean@1", "policy_clean@final",
            "requirement_coverage@final", "all_checks_passed@1", "all_checks_passed@final", "avg_attempts",
            "avg_latency_s", "medium_findings_final"]
    out = [table(["Metric", *variants], [[k, *[s[v][k] for v in variants]] for k in keys])]
    if s.get("failure_examples"):
        out.append("\nFirst-attempt failures (fixed by the loop or not):\n" + "\n".join(
            f"- `{f['id']}` errors={f['first_attempt_errors']} policy={f['first_high_policy']} "
            f"req={f['first_req_failed']} -> final passed={f['final_passed']}" for f in s["failure_examples"]))
    return "\n".join(out)


def judge_md(s: dict[str, Any], extra: list[str]) -> str:
    rows = [["faithfulness", s["faithfulness"]], ["coverage", s["coverage"]], ["clarity", s["clarity"]]]
    rows += [[k, s[k]] for k in extra]
    out = [table(["Metric", "Value"], rows)]
    out.append("\nLowest-scored:\n" + "\n".join(f"- `{l['id']}` faithfulness={l['faithfulness']}: {l['rationale']}" for l in s["lowest"]))
    return "\n".join(out)


def simple_md(s: dict[str, Any], skip: tuple[str, ...] = ()) -> str:
    return "```json\n" + json.dumps({k: v for k, v in s.items() if k not in skip}, indent=1) + "\n```"


SECTIONS = [
    ("1. Intent Router", "router", eval_router, router_md),
    ("2. Requirements Collector", "collector", eval_collector, lambda s: simple_md(s)),
    ("3. Retriever (BM25 vs dense vs hybrid)", "retrieval", eval_retrieval, retrieval_md),
    ("3b. Retriever, offline BM25-only run", "retrieval_bm25", eval_retrieval, retrieval_md),
    ("4. Semantic Cache", "cache", eval_cache, cache_md),
    ("5. Terraform Generator + Validator loop", "generation", eval_generation, generation_md),
    ("6. Validator (labelled good/bad configs)", "validator", eval_validator, lambda s: simple_md(s)),
    ("7. Explainer (LLM judge)", "explainer", eval_explainer,
     lambda s: judge_md(s, ["share_faithfulness>=4", "explanations_with_hallucinated_resources"])),
    ("8. Advisor (question path)", "advisor", eval_advisor,
     lambda s: judge_md(s, ["answers_with_citation", "invalid_citation_rate"])),
]


def main() -> None:
    summary: dict[str, Any] = {}
    parts = [f"# Evaluation report\n\nGenerated {datetime.now():%Y-%m-%d %H:%M} from `evals/results/*.jsonl` "
             "by `python -m evals.make_report`.\n"]
    for title, name, module, render in SECTIONS:
        rows = load_results(name)
        if not rows:
            parts.append(f"## {title}\n\n_not run yet_\n")
            continue
        s = module.summarize(rows)
        summary[name] = s
        parts.append(f"## {title}\n\n{render(s)}\n")
    (ROOT / "docs" / "eval_report.md").write_text("\n".join(parts), encoding="utf-8")
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("wrote docs/eval_report.md")


if __name__ == "__main__":
    main()
