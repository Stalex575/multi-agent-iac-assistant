# CRISP-DM log

Chronological log of how the project moved through CRISP-DM phases (written by the
author and the coding agent via the `crisp-dm-log` skill).

### Step 1 - Business Understanding - choose the task
- **What:** Picked the "DevOps assistant that generates Terraform" brief and decided to
  upgrade the author's earlier `multi-agent-triage` (LangGraph router → specialist →
  QA-retry loop + semantic cache) instead of starting from scratch.
- **Why / decision:** reusing working parts made more sense than starting over. The old repo already had routing, retrieval, a
  self-correcting retry loop and caching, but had no evaluation, no requirement
  collection, toy seed data (6 hand-written docs) and only an LLM-as-QA check.
  Rejected: a brand-new HR/recruiter assistant (no reusable parts).
- **Success criteria (business → metric):**
  - "Collects requirements" → slot F1 ≥ 0.85 vs gold conversations; asks follow-ups only for missing slots.
  - "Retrieves best-practice docs" → Recall@5 ≥ 0.8 on labelled queries.
  - "Valid Terraform" → `terraform validate` pass rate ≥ 90% after retry loop.
  - "Respects requirements" → requirement-coverage checks ≥ 85%.
  - "Explains choices" → LLM-judge faithfulness ≥ 4/5.
  - Out-of-scope requests are refused (router OUT_OF_SCOPE recall ≥ 0.9).
- **Next:** inspect available data.

### Step 2 - Data Understanding - knowledge sources
- **What:** Probed public sources. AWS Well-Architected Framework TOC has 366 leaf pages
  (Reliability 72, Security 73, Cost 58, Performance 40 …). Terraform AWS provider docs
  are markdown on GitHub (e.g. `db_instance` = 43 KB with example usage + argument reference).
- **Why / decision:** Real docs beat synthetic ones (assignment asks to check existing
  datasets first). Keep 4 pillars relevant to "small AWS service"; ~53 resource docs.
  No public dataset of (requirements → Terraform) pairs with gold labels fits, so eval
  sets are synthetic but hand-checked (see `data/eval/`).
- **Evidence:** `scripts/fetch_docs.py`, `data/raw/`.

### Step 3 - Deployment (tooling) - reproducible validator
- **What:** Installed Terraform 1.9.8, pre-initialised `terraform_sandbox/` with
  `hashicorp/aws ~> 5.0`; generated code never declares providers, so validation runs offline.
- **Why / decision:** Deterministic validity metric instead of LLM-judged validity.

### Step 4 - Data Preparation - chunking
- **What:** `scripts/build_index.py` turns 263 raw docs into 2315 chunks (1168 Terraform, 1147 Well-Architected).
  Terraform docs are split by `##`/`###` headings; "Attribute Reference", "Import" and "Timeouts" sections are dropped
  because they describe outputs, not how to write config. WA pages are re-flowed (the HTML export put a newline
  after every inline tag) and cut into ~1200 char windows with 200 char overlap.
- **Why / decision:** the generator needs argument names and examples, the advisor needs best-practice prose.
  Heading-based splitting keeps an "Example Usage" block together.
- **Evidence:** median chunk 1105 chars, max 1200.

### Step 5 - Modeling - graph rebuilt on top of the old triage router
- **What:** kept the old ideas (router, specialist, QA retry loop, semantic cache) and changed what they do:
  - router now picks TERRAFORM_REQUEST / INFRA_QUESTION / OUT_OF_SCOPE instead of DB vs DevOps;
  - new Requirements Collector (structured slot filling, asks follow-ups, max 3 rounds, then fills defaults and
    marks them as assumptions);
  - the LLM "QA lead" was replaced by a deterministic Validator (`terraform fmt`, `terraform validate`, policy rules,
    requirement checks). Failures go back to the generator with the exact error list (max 2 fixes);
  - Pinecone was replaced by a local hybrid index (BM25 + Gemini embeddings, fused with RRF). One API key instead of two,
    and retrieval evals are reproducible.
- **Why / decision:** an LLM judging its own Terraform is not reliable; `terraform validate` is.
- **Evidence:** `src/infra_assistant/graph.py`, 13 offline tests pass (`pytest -q`).

### Step 6 - Evaluation - first offline results
- **What:** validator eval (21 labelled configs) and BM25 retrieval eval (56 queries) run without an API key.
- **Findings:**
  - The first validator run "failed" 2 cases. Both were labelling mistakes on my side: an S3 bucket with public
    access blocked was labelled as satisfying `network_access=public`, but nothing in it is reachable from the
    internet. The validator was right. Fixed the labels and added a case for exactly that. Now 21/21.
  - BM25 gets Hit@5 = 1.0 on queries that contain resource names, but only 0.42 on 12 paraphrased queries
    ("get an email when CPU is too high"). The first version of the retrieval set was too easy because I wrote the
    queries with the doc titles in mind. Added the paraphrased group to make the comparison with dense retrieval fair.
- **Next:** embed the corpus and check whether dense / hybrid fixes the paraphrased group.

### Step 7 - Modeling / Evaluation - first real run, one-line HCL bug
- **What:** first end-to-end run with real Gemini (Flask + Postgres, multi-AZ). The first generator draft came back
  as a single line: the model put escaped `\n` into the structured-output string, so `terraform validate` failed for
  a reason that has nothing to do with Terraform. The fix loop recovered on attempt 2, but it wastes a call.
- **Fix:** `extract_hcl` restores escaped newlines when the draft has no real line breaks (+ regression test).
- **Models chosen (free tier limits):** generator/explainer/advisor `gemini-3.5-flash-lite` (15 RPM, 500 RPD),
  router/collector `gemini-3.1-flash-lite` (separate quota), judges `gemini-3.8-flash` and `gemini-4-hydrogen`
  (5 RPM, 20 RPD each, so only used for ~12 judge calls each). Stronger "flash" models were not used for generation
  because 20 requests/day is not enough for 12 generation cases with retries.

### Step 8 - Data Preparation (loop back) - embedding quota
- **What:** started embedding all 2315 chunks and hit the free-tier limit after ~200: the embedding API counts every
  text in a batch as one request (100/min, 1000/day). Embedding everything would need 2315 requests, more than a day
  of quota. Stopped the job.
- **Decision:** dense vectors are now built per document (263 texts: title + start of the doc, ~1500 chars), BM25
  stays per chunk. Hybrid search fuses both, so the dense side picks the right document and BM25 picks the right
  paragraph. Embedding is paced at 15 texts / 15 s. Total embedding use ~680 of the 1000 daily requests incl. evals and demo.
- **Trade-off:** dense retrieval loses detail from the later sections of long docs. The retrieval eval measures
  at document level, so it is still a fair comparison, but chunk-level dense would likely score a bit higher.

### Step 9 - Evaluation -> Modeling (loop back) - router too easy, hybrid worse than dense, cache threshold
- **Router:** 60/60 correct vs 88.3% for a keyword baseline. A perfect score on a hand-written set says more about
  the set than the router, so 15 deliberately ambiguous messages were added (questions phrased like requests,
  Terraform debugging, coding tasks next to infra, follow-ups without context). Reported separately as "hard".
- **Collector:** slot accuracy 1.0 lenient / 0.945 strict. All strict misses are `public` vs
  `public_frontend_private_backend` (5/14) - the two enum values overlap. Could be merged, the policy rules already
  force the DB to be private.
- **Retrieval:** dense Hit@5 1.0 (paraphrased 1.0) vs BM25 0.875 (paraphrased 0.42), but hybrid only 0.857.
  Cause: RRF was done over chunks; BM25 votes once per chunk, dense once per document (doc-level vectors), so BM25
  noise outvoted good dense hits. Fix: RRF over documents, then the best BM25 paragraph of each winning doc.
  Simulated on the stored rankings before spending quota: Hit@5 0.857 -> 0.982 with equal weights (dense x2 would
  give 1.0, but that is tuning on the test set, so equal weights were kept). Old run kept as
  `retrieval_v1_chunk_fusion.jsonl` for the report.
- **Cache:** paraphrases average cosine 0.961, near-miss questions 0.874 but up to 0.923. At the old threshold 0.92
  the false-hit rate is 13% (e.g. "PITR for DynamoDB" vs "PITR for RDS"). From 0.93 up there are no false hits on
  this set; 0.94 chosen to keep some margin (recall drops to 0.867, which only costs an extra LLM call).

### Step 10 - Evaluation - generation, explainer, advisor; judge problems
- **Generation:** all checks passed 0.25 on the first draft -> 0.83 after the fix loop (no-RAG ablation: 0.00 -> 0.58).
  No HIGH policy finding and 100% requirement coverage in every final config. Remaining failures: a Lambda case using
  the `archive` provider (not installed in the offline sandbox) and a non-existent `inline_code` block, and an ECS
  case that kept putting `subnet_ids` in the wrong block. Without RAG the model often wrote the whole config on one line.
- **Judges:** `gemini-3.8-flash` and then `gemini-3.7-flash` returned 503 "high demand" for every call, so the
  explainer was judged by `gemini-3.6-flash` and the advisor by `gemini-4-hydrogen`. 3.6-flash gave 5/5 to all 12
  explanations; Hydrogen re-judged 4 of them at 4/5. Conclusion: LLM-judge numbers depend on the judge model, and
  the judge needs the same context as the explainer (it flagged the injected region as unsupported).
- **Advisor:** faithfulness 2.5/5. Mostly correct facts that are not in the indexed docs (the knowledge base has
  the Terraform reference and Well-Architected, not the service user guides), plus 2 real grounding errors.
- **Next (future work):** add service user guides to the corpus; re-retrieve docs for the resource named
  in a repeated validate error; pre-install `hashicorp/archive` in the sandbox; clearer scope examples in the router.
