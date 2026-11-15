# CRISP-DM log

Chronological log of how the project moved through CRISP-DM phases (written by the
author and the coding agent via the `crisp-dm-log` skill).

### 16:05 — Business Understanding — choose the task
- **What:** Picked the "DevOps assistant that generates Terraform" brief and decided to
  upgrade the author's earlier `multi-agent-triage` (LangGraph router → specialist →
  QA-retry loop + semantic cache) instead of starting from scratch.
- **Why / decision:** ~8h deadline. The old repo already had routing, retrieval, a
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

### 16:20 — Data Understanding — knowledge sources
- **What:** Probed public sources. AWS Well-Architected Framework TOC has 366 leaf pages
  (Reliability 72, Security 73, Cost 58, Performance 40 …). Terraform AWS provider docs
  are markdown on GitHub (e.g. `db_instance` = 43 KB with example usage + argument reference).
- **Why / decision:** Real docs beat synthetic ones (assignment asks to check existing
  datasets first). Keep 4 pillars relevant to "small AWS service"; ~53 resource docs.
  No public dataset of (requirements → Terraform) pairs with gold labels fits, so eval
  sets are synthetic but hand-checked (see `data/eval/`).
- **Evidence:** `scripts/fetch_docs.py`, `data/raw/`.

### 16:25 — Deployment (tooling) — reproducible validator
- **What:** Installed Terraform 1.9.8, pre-initialised `terraform_sandbox/` with
  `hashicorp/aws ~> 5.0`; generated code never declares providers, so validation runs offline.
- **Why / decision:** Deterministic validity metric instead of LLM-judged validity.
