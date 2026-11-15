# multi-agent-iac-assistant (InfraPilot)

InfraPilot turns a plain-English description of a small AWS system into a Terraform configuration that
actually passes `terraform validate`, plus a short explanation of the design. If the description is missing
something important (availability, network access, database, budget...), it asks follow-up questions first.
It can also answer general AWS / DevOps questions with citations to the AWS Well-Architected Framework and
the Terraform AWS provider docs.

This is an upgrade of my earlier project [multi-agent-triage](https://github.com/Stalex575/multi-agent-triage)
(LangGraph router + two specialist agents + LLM QA retry loop + Pinecone semantic cache). What changed and why
is described in [Section 4](#4-what-was-reused-from-the-old-project-and-what-changed).

- Detailed evaluation numbers: [`docs/eval_report.md`](docs/eval_report.md)
- CRISP-DM log (how the project moved through the phases): [`docs/crisp_dm_log.md`](docs/crisp_dm_log.md)
- Coding agent setup: [`CLAUDE.md`](CLAUDE.md), [`.claude/skills/`](.claude/skills)

---

## 1. Task and scope

**User:** a developer who has to put a small service on AWS and does not want to write Terraform from scratch.

**Input:** chat messages, e.g. *"I need to host a Django web shop with Postgres, it must survive an AZ outage,
about 200 req/s, budget $400/month"*.

**Output:** a Terraform config (one file), the validator result, and an explanation that maps every requirement
to the resources that implement it, with a rough cost estimate.

Business rules kept in scope:

| Rule | How it is implemented |
|---|---|
| Collect requirements before generating anything | 6 required slots: workload, traffic, network access, availability, database, monthly budget. Optional: compute type, object storage, region, storage size. |
| Do not pester the user forever | At most 3 rounds of follow-up questions. After that, missing slots are filled with defaults and clearly listed as **assumptions** in the answer. |
| User can change their mind | The latest statement wins (e.g. "actually use MySQL"). Requirements are kept per conversation, so "make the DB multi-AZ" regenerates the config. |
| Config must be valid | `terraform fmt` + `terraform validate` against the real AWS provider (v5). On failure the errors go back to the generator, max 2 fix rounds. |
| Config must be safe by default | Policy rules from Well-Architected: encrypted and private databases, backups on, S3 public access blocked, no SSH/RDP/DB ports open to 0.0.0.0/0, encrypted EC2 disks. |
| Config must match what the user asked for | Requirement checks: multi-AZ really means multi-AZ subnets and `multi_az = true` on RDS, private means internal load balancers, the right DB engine, etc. |
| Only infra topics | Off-topic requests (recipes, homework, prompt injection) are refused politely. |
| Questions get grounded answers | Retrieval over AWS docs, inline citations `[n]`, repeated questions are served from a semantic cache. |

Out of scope: actually running `terraform apply`, multi-file modules, other clouds, exact cost calculation.

---

## 2. Architecture

```mermaid
flowchart TD
    U([User message]) --> R{Intent Router<br/>LLM, structured}
    R -- OUT_OF_SCOPE --> OOS[Polite refusal]
    R -- TERRAFORM_REQUEST --> C[Requirements Collector<br/>LLM slot filling]
    C -- slots missing --> Q[/Follow-up question/] --> F
    C -- complete --> RT[Retriever<br/>BM25 + dense, RRF]
    RT --> G[Terraform Generator<br/>LLM]
    G --> V[Validator<br/>terraform fmt + validate<br/>policy + requirement checks]
    V -- failed, retries left --> G
    V -- passed / out of retries --> E[Explainer<br/>LLM]
    E --> F
    R -- INFRA_QUESTION --> SC{Semantic cache<br/>embedding similarity}
    SC -- hit --> F
    SC -- miss --> RQ[Retriever] --> A[Advisor<br/>LLM with citations] --> CW[Cache write] --> F
    OOS --> F([Answer + trace])

    KB[(Knowledge base<br/>2315 chunks<br/>WA Framework + TF docs)] -.-> RT
    KB -.-> RQ
    TF[[terraform CLI<br/>AWS provider 5.x]] -.-> V
```

Every arrow is a LangGraph edge. State is kept per conversation thread (LangGraph `MemorySaver`), so the
collector remembers what was said in earlier turns.

### Components

| # | Component | Purpose | Input | Output | Model / tool call |
|---|---|---|---|---|---|
| 1 | Intent Router | Decide which path handles the message | user message + last turns | `TERRAFORM_REQUEST` / `INFRA_QUESTION` / `OUT_OF_SCOPE` + reason | Gemini flash-lite, structured output. Skipped while the collector waits for answers. |
| 2 | Requirements Collector | Fill the requirement slots, ask for missing ones | current slots + conversation | updated slots, follow-up question or "complete" + list of assumed slots | Gemini flash-lite, structured output (`CollectorResult`). Missing slots and defaults are computed in code, not by the LLM. |
| 3 | Retriever | Give the generator real argument names and best practices | requirements turned into 4-6 queries (or the user question) | top chunks with source URLs | BM25 over 2315 chunks (local) + Gemini embeddings over 263 documents (cosine), fused with Reciprocal Rank Fusion |
| 4 | Terraform Generator | Write the config | requirements, assumptions, retrieved docs, validator errors on retries | HCL + design notes | Gemini flash, structured output (`TerraformDraft`) |
| 5 | Validator | Check validity, safety, and that requirements are met | HCL + requirements | report: syntax, validate errors, policy findings, requirement checks, pass/fail | **No LLM.** `terraform fmt`, `terraform validate -json`, HCL parsing with `python-hcl2` |
| 6 | Explainer | Explain the design to the user | requirements, config, validator report | markdown explanation + cost estimate | Gemini flash |
| 7 | Semantic Cache | Skip the LLM for repeated questions | question | cached answer if cosine >= threshold | Gemini embeddings |
| 8 | Advisor | Answer infra questions with citations | question + retrieved chunks | answer with `[n]` citations + source list | Gemini flash |

### One input, end to end

Message: *"I need to host a Django web shop with PostgreSQL. It must survive an AZ outage, ~200 req/s, public
website, $400/month."*

1. **Router** sees a build request and returns `TERRAFORM_REQUEST`.
2. **Collector** extracts `workload=Django web shop, traffic=200 req/s, availability=multi_az, database=postgres,
   network_access=public_frontend_private_backend, monthly_budget_usd=400`. Nothing required is missing, so no
   follow-up. Region was not given, so `eu-central-1` is added and listed as an assumption.
3. **Retriever** builds queries like *"aws_db_instance postgres multi_az storage_encrypted ..."*,
   *"aws_launch_template aws_autoscaling_group aws_lb_target_group ..."* and *"deploy the workload to multiple
   Availability Zones"*, and returns chunks from `aws_db_instance`, `aws_autoscaling_group`, `REL10-BP01`, etc.
4. **Generator** writes VPC + subnets in 2 AZs + ALB + autoscaling group + RDS Postgres.
5. **Validator** runs `terraform validate`. Say the model used an argument that does not exist in provider v5:
   the error (`Unsupported argument ... line 42`) goes back to the generator together with the previous draft.
   Second draft passes validate, policy rules and the requirement checks (`multi_az.subnets`, `multi_az.database`,
   `database=postgres`, `network=public_frontend_private_backend`).
6. **Explainer** writes the architecture summary, which resource covers which requirement, the validator result
   and a cost range. The UI shows the answer and, on the right, the path through the graph, the timing of each
   node, the collected slots, retrieved docs and the full validator report.

---

## 3. Data

| Data | Source | Size | Used for |
|---|---|---|---|
| AWS Well-Architected Framework best practices (Reliability, Security, Cost, Performance) | docs.aws.amazon.com, downloaded by `scripts/fetch_docs.py` | 210 pages | best-practice context, advisor answers |
| Terraform AWS provider resource docs | `hashicorp/terraform-provider-aws` on GitHub | 53 resources | exact argument names and examples for the generator |
| Knowledge base after cleaning and chunking | `scripts/build_index.py` | 2315 chunks (1168 TF, 1147 WA), 263 document vectors | retrieval |
| Evaluation sets | written by hand, see below | 60 + 14 + 56 + 30 + 12 + 21 items | evaluation |

I looked for an existing dataset of "requirements -> Terraform" pairs with labels and did not find one that fits
small AWS services, so the evaluation sets in `data/eval/` are synthetic but written and checked by hand:

- `router.jsonl` - 60 messages (24 build requests, 22 infra questions, 14 off-topic incl. a prompt injection).
- `collector.jsonl` - 14 scripted conversations (1 to 3 user turns) with gold slot values, including a user who
  changes the database mid-conversation and one who starts with "I want to deploy something".
- `retrieval.jsonl` - 56 queries with relevant documents: 26 Terraform-syntax queries, 18 best-practice queries,
  12 paraphrased queries that do not use AWS vocabulary.
- `cache_pairs.jsonl` - 15 paraphrase pairs and 15 "near miss" pairs (same service, different question).
- `generation.jsonl` - 12 complete requirement sets covering EC2, ECS Fargate, Lambda, static site, private tools.
- validator cases - 21 configs built by breaking known-good configs on purpose (in `evals/eval_validator.py`).

---

## 4. What was reused from the old project and what changed

| Old (multi-agent-triage) | New | Why |
|---|---|---|
| Router: DB_INFRA vs CLOUD_DEVOPS | Router: build / question / off-topic | The new task needs to separate "build me something" from "explain something", and refuse off-topic input. |
| Single-shot query | Multi-turn requirements collector | The assignment brief needs requirements collected before generation. |
| 2 specialist agents writing free text | Terraform generator with structured output | Output has to be a config, not advice. |
| LLM "QA lead" deciding if the answer is good | Deterministic validator (terraform CLI + rules) | An LLM grading its own Terraform is unreliable. `terraform validate` gives a hard yes/no and exact errors to fix. |
| QA retry loop | Kept, now driven by validator errors | Same idea, much more useful feedback. |
| Pinecone (cache + 2 namespaces, 6 hand-written docs) | Local hybrid index over 2315 real doc chunks + local semantic cache | One API key instead of two, reproducible evals, BM25 helps with exact resource names. Dense vectors are per document (263) because the free embedding quota is 1000 texts/day. |
| No evaluation | 8 evals, one per component | Requirement of the assignment. |
| FastAPI endpoint | Gradio chat UI with a trace panel, CLI | Easier to demo and to see what each node did. |

---

## 5. Setup and run

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/) (or pip), Terraform >= 1.5 on PATH, a Google AI
Studio API key.

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements.txt -e .
cp .env.example .env            # then put your GOOGLE_API_KEY into .env

# one-time: provider for offline `terraform validate`
terraform -chdir=terraform_sandbox init

# knowledge base (chunks are already in the repo; this downloads raw docs again and embeds them)
python scripts/fetch_docs.py    # optional, only if you want to rebuild from scratch
python scripts/build_index.py   # embeds 263 documents with Gemini (~5 min, paced for the free tier), resumable

python -m infra_assistant.ui    # web UI on http://127.0.0.1:7860
python -m infra_assistant.cli   # or a terminal chat
pytest -q                       # offline tests, no key needed
```

Optional LangSmith tracing: set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` in `.env`.
No code changes are needed, LangGraph sends every node and LLM call (prompt, output, tokens, latency) to
LangSmith. Evals were traced into a separate project (`LANGSMITH_PROJECT=iac-evals python -m evals.run_all`)
so the demo project only has demo conversations.

### Evaluation

```bash
python -m evals.run_all --offline           # validator + BM25 retrieval, no key needed
python -m evals.run_all --ablation          # everything, plus generation without RAG
python -m evals.make_report                 # writes docs/eval_report.md
```

Every eval writes one JSON line per item to `evals/results/` and skips items that are already there, so a run
that died because of the network can just be started again.

---

## 6. Evaluation

Each component has its own metric, chosen so that a bad component would actually get a low score.

| Component | Metric(s) | Why this metric |
|---|---|---|
| Intent Router | accuracy, macro-F1, confusion matrix, compared with a keyword baseline | 3-class classification; macro-F1 so the small OUT_OF_SCOPE class counts; the baseline shows whether the LLM is worth it |
| Requirements Collector | slot accuracy (strict and lenient), missing-slot detection P/R/F1 on turn 1, hallucinated slots, fully correct conversations, follow-ups per conversation | the two failure modes are inventing values and asking for things the user already said |
| Retriever | Hit@1, Hit@5, Recall@5, MRR, nDCG@5 for BM25, dense and hybrid | standard ranking metrics; comparing three modes shows why hybrid was chosen |
| Semantic Cache | precision / recall / F1 and false-hit rate over a threshold sweep | a false hit returns the answer to a different question, so false-hit rate matters more than recall |
| Generator + Validator loop | validate pass@1 vs pass@final, policy-clean rate, requirement coverage, attempts, latency; ablation without RAG | shows how good the first draft is, how much the fix loop adds and how much retrieval adds |
| Validator | decision accuracy, fail-detection P/R/F1, stage accuracy on 21 labelled configs | the validator is the "judge" of the generator, so it has to be checked itself |
| Explainer | LLM judge (faithfulness, coverage, clarity 1-5) + count of `aws_*` resources mentioned but not in the config | prose has no exact answer; the deterministic hallucination check backs up the judge |
| Advisor | LLM judge + share of answers with citations + invalid citation rate | the answer must be grounded in the retrieved docs |

### Results

RESULTS_PLACEHOLDER

---

## 7. How the coding agent was set up

The code was written together with an AI coding agent (Claude Code). I steered it with files, not only with
chat prompts:

- **[`CLAUDE.md`](CLAUDE.md)** (and `AGENTS.md` pointing to it for other tools) is loaded on every session. It
  contains the project summary, the mandatory CRISP-DM order of work, hard rules (never read `.env`, one LLM
  provider, structured outputs for anything that drives control flow, deterministic checks instead of LLM checks
  where possible, no provider blocks in generated Terraform, tests must run offline, evals must be resumable),
  code conventions (where prompts, nodes and wiring live) and a definition of done.
- **Skills** in `.claude/skills/`, small playbooks the agent loads when a task matches:
  - `add-graph-node` - the 6 steps for a new pipeline component (contract, schema, prompt, node, wiring, test + eval);
  - `run-evals` - how to run, read and interpret the evals, including a metric cheat sheet;
  - `crisp-dm-log` - the template for entries in `docs/crisp_dm_log.md`.
- **Context control:** the agent got the assignment PDF and the old repo as context; the old repo was copied to
  a git-ignored `legacy/` folder to be used as reference only. Personal rules were stored separately (no AI
  co-author lines in commits, nothing is pushed without my permission, never look into API keys).
- **What worked / what did not:** the rule "deterministic check first" led to the validator replacing the LLM QA
  node, which was the biggest quality gain. Writing the eval sets first caught a mistake early: the first retrieval
  set was too easy (queries contained resource names), so a paraphrased group was added. The agent sometimes
  over-engineered things, so I asked for simpler code and kept the review on my side.

---

## 8. Limitations and next steps

- Eval sets are small (written by hand in one day); numbers have wide error bars.
- Requirement checks cover the main slots, not everything (e.g. budget is only used in the prompt, there is no
  real cost check; Infracost would be the next step).
- The validator only checks what `terraform validate` can check without AWS credentials (no `plan`).
- One Terraform file per answer; no modules or remote state.
- Gemini free tier rate limits make the full eval run slow (~15 requests/minute).
