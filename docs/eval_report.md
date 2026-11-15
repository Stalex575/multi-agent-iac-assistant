# Evaluation report

Generated 2026-11-15 14:42 from `evals/results/*.jsonl` by `python -m evals.make_report`.

## 1. Intent Router

_not run yet_

## 2. Requirements Collector

_not run yet_

## 3. Retriever (BM25 vs dense vs hybrid)

_not run yet_

## 3b. Retriever, offline BM25-only run

| Metric | bm25 |
|---|---|
| hit@1 | 0.661 |
| hit@5 | 0.875 |
| recall@5 | 0.811 |
| mrr | 0.753 |
| ndcg@5 | 0.738 |
| hit@5_terraform_docs | 1.0 |
| hit@5_well_architected | 1.0 |
| hit@5_paraphrased | 0.417 |

Misses (best mode):
- `p01` "keep my database running even if one data center goes down" -> top3 ['tf:aws_ecs_service', 'wa:perf_data_use_purpose_built_data_store', 'tf:aws_security_group']
- `p04` "where should I keep the password for my database so it is not in code" -> top3 ['wa:rel_monitor_aws_resources_storage_analytics', 'wa:rel_planning_for_recovery_disaster_recovery', 'wa:cost_decomissioning_resources_implement_process']
- `p06` "let private machines reach the internet for updates without being reachable themselves" -> top3 ['wa:sec_securely_operate_reduce_management_scope', 'wa:cost_data_transfer_optimized_components', 'tf:aws_lb']
- `p07` "get an email when the server CPU is too high" -> top3 ['wa:rel_mitigate_interaction_failure_client_timeouts', 'tf:aws_ecs_service', 'tf:aws_lb_listener']
- `p08` "serve my website quickly to users all over the world" -> top3 ['wa:cost_evaluate_new_services_review_workload', 'wa:rel_planning_network_topology_ha_conn_users', 'tf:aws_s3_bucket']
- `p09` "run code only when a request comes in and pay per invocation" -> top3 ['wa:sec_incident_response_prepare_forensic', 'wa:rel_planning_for_recovery_disaster_recovery', 'wa:rel_prevent_interaction_failure_identify']
- `p10` "how much data can we afford to lose and how long can we be down" -> top3 ['wa:sec_securely_operate_threat_model', 'wa:rel_withstand_component_failures_failover2good', 'wa:cost_data_transfer_modeling']

## 4. Semantic Cache

_not run yet_

## 5. Terraform Generator + Validator loop

_not run yet_

## 6. Validator (labelled good/bad configs)

```json
{
 "n": 21,
 "decision_accuracy": 1.0,
 "fail_detection": {
  "precision": 1.0,
  "recall": 1.0,
  "f1": 1.0
 },
 "stage_accuracy": 1.0,
 "mistakes": []
}
```

## 7. Explainer (LLM judge)

_not run yet_

## 8. Advisor (question path)

_not run yet_
