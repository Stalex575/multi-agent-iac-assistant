# Evaluation report

Generated 2026-11-15 20:40 from `evals/results/*.jsonl` by `python -m evals.make_report`.

## 1. Intent Router

|  | Accuracy | Macro-F1 |
|---|---|---|
| LLM router | 0.947 | 0.941 |
| Keyword baseline | 0.787 | 0.787 |

Per class (LLM):

| Class | P | R | F1 |
|---|---|---|---|
| TERRAFORM_REQUEST | 0.933 | 1.0 | 0.966 |
| INFRA_QUESTION | 0.966 | 0.933 | 0.949 |
| OUT_OF_SCOPE | 0.938 | 0.882 | 0.909 |

Confusion matrix (rows = gold, cols = predicted):

| gold \ pred | TERRAFORM_REQUEST | INFRA_QUESTION | OUT_OF_SCOPE |
|---|---|---|---|
| TERRAFORM_REQUEST | 28 | 0 | 0 |
| INFRA_QUESTION | 1 | 28 | 1 |
| OUT_OF_SCOPE | 1 | 1 | 15 |

Errors:
- `h05` "Write a Python script that uploads a file to S3 with boto3" gold=OUT_OF_SCOPE pred=INFRA_QUESTION
- `h10` "Write a Dockerfile for my Flask app" gold=OUT_OF_SCOPE pred=TERRAFORM_REQUEST
- `h12` "Can you review this terraform? resource "aws_s3_bucket" "b" { acl = "public-read" }" gold=INFRA_QUESTION pred=TERRAFORM_REQUEST
- `h15` "how do I deploy a kubernetes cluster on my raspberry pi at home" gold=INFRA_QUESTION pred=OUT_OF_SCOPE

## 2. Requirements Collector

```json
{
 "n_conversations": 14,
 "slot_accuracy_strict": 0.945,
 "slot_accuracy_lenient": 1.0,
 "per_slot_accuracy": {
  "workload": 1.0,
  "traffic": 1.0,
  "network_access": 1.0,
  "availability": 1.0,
  "database": 1.0,
  "monthly_budget_usd": 1.0,
  "compute": 1.0,
  "object_storage": 1.0,
  "region": 1.0
 },
 "missing_slot_detection_turn1": {
  "precision": 1.0,
  "recall": 0.955,
  "f1": 0.977
 },
 "hallucinated_slots_turn1": 2,
 "conversations_fully_correct": 1.0,
 "avg_followup_questions": 1.07,
 "errors": []
}
```

## 3. Retriever (BM25 vs dense vs hybrid)

| Metric | bm25 | dense | hybrid |
|---|---|---|---|
| hit@1 | 0.661 | 0.893 | 0.786 |
| hit@5 | 0.875 | 1.0 | 0.982 |
| recall@5 | 0.807 | 0.941 | 0.879 |
| mrr | 0.756 | 0.942 | 0.877 |
| ndcg@5 | 0.737 | 0.914 | 0.828 |
| hit@5_terraform_docs | 1.0 | 1.0 | 1.0 |
| hit@5_well_architected | 1.0 | 1.0 | 1.0 |
| hit@5_paraphrased | 0.417 | 1.0 | 0.917 |

Misses (best mode):
- `p06` "let private machines reach the internet for updates without being reachable themselves" -> top3 ['wa:sec_network_protection_create_layers', 'wa:sec_protect_data_transit_key_cert_mgmt', 'wa:rel_planning_network_topology_ip_subnet_allocation']

## 3b. Retriever, first version (chunk-level fusion, before the fix)

| Metric | bm25 | dense | hybrid |
|---|---|---|---|
| hit@1 | 0.661 | 0.893 | 0.571 |
| hit@5 | 0.875 | 1.0 | 0.857 |
| recall@5 | 0.807 | 0.941 | 0.798 |
| mrr | 0.756 | 0.942 | 0.703 |
| ndcg@5 | 0.737 | 0.914 | 0.7 |
| hit@5_terraform_docs | 1.0 | 1.0 | 0.962 |
| hit@5_well_architected | 1.0 | 1.0 | 1.0 |
| hit@5_paraphrased | 0.417 | 1.0 | 0.417 |

Misses (best mode):
- `t09` "autoscaling group with launch template min max desired capacity" -> top3 ['tf:aws_launch_template', 'tf:aws_autoscaling_policy', 'wa:rel_adapt_to_changes_proactive_adapt_auto']
- `p01` "keep my database running even if one data center goes down" -> top3 ['wa:rel_planning_network_topology_ha_conn_users', 'wa:rel_fault_isolation_single_az_system', 'wa:rel_mitigate_interaction_failure_graceful_degradation']
- `p02` "make sure nobody on the internet can read the files in my storage bucket" -> top3 ['wa:sec_network_protection_inspection', 'wa:perf_data_use_purpose_built_data_store', 'tf:aws_s3_bucket_versioning']
- `p05` "run my docker containers without managing servers" -> top3 ['wa:rel_mitigate_interaction_failure_stateless', 'tf:aws_lambda_function', 'wa:cost_select_service_select_for_cost']
- `p06` "let private machines reach the internet for updates without being reachable themselves" -> top3 ['wa:sec_appsec_centralize_services_for_packages_and_dependencies', 'wa:sec_protect_data_transit_key_cert_mgmt', 'wa:sec_network_protection_layered']
- `p08` "serve my website quickly to users all over the world" -> top3 ['wa:cost_data_transfer_implement_services', 'wa:rel_withstand_component_failures_failover2good', 'wa:cost_pricing_model_region_cost']
- `p09` "run code only when a request comes in and pay per invocation" -> top3 ['wa:cost_pricing_model_analysis', 'wa:rel_tracking_change_management_functional_testing', 'wa:cost_manage_demand_resources_dynamic']
- `p11` "spending is growing too fast, how do we keep it under control" -> top3 ['wa:rel_mitigate_interaction_failure_fail_fast', 'wa:cost_govern_usage_goal_target', 'wa:cost_monitor_usage_config_tools']

## 3c. Retriever, offline BM25-only run

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

Mean cosine: paraphrases 0.961, near-miss negatives 0.874 (max negative 0.923).

| Threshold | Precision | Recall | F1 | False-hit rate |
|---|---|---|---|---|
| 0.85 | 0.556 | 1.0 | 0.714 | 0.8 |
| 0.88 | 0.682 | 1.0 | 0.811 | 0.467 |
| 0.9 | 0.833 | 1.0 | 0.909 | 0.2 |
| 0.92 | 0.882 | 1.0 | 0.938 | 0.133 |
| 0.94 | 1.0 | 0.867 | 0.929 | 0.0 |
| 0.96 | 1.0 | 0.533 | 0.696 | 0.0 |

Best F1: {'threshold': np.float64(0.93), 'precision': np.float64(1.0), 'recall': np.float64(1.0), 'f1': np.float64(1.0), 'false_hit_rate': np.float64(0.0)}  
Lowest threshold with zero false hits: {'threshold': np.float64(0.93), 'precision': np.float64(1.0), 'recall': np.float64(1.0), 'f1': np.float64(1.0), 'false_hit_rate': np.float64(0.0)}

Hardest negatives:
- 0.9234: "How do I enable point-in-time recovery for DynamoDB?" vs "How do I enable point-in-time recovery for RDS?"
- 0.922: "Is Fargate more expensive than EC2 for a constant workload?" vs "Is Lambda more expensive than EC2 for a constant workload?"
- 0.9155: "What is a DB subnet group for?" vs "What is a DB parameter group for?"

## 5. Terraform Generator + Validator loop

| Metric | rag | no_rag |
|---|---|---|
| n | 12 | 12 |
| validate_pass@1 | 0.333 | 0.083 |
| validate_pass@final | 0.833 | 0.583 |
| policy_clean@1 | 1.0 | 1.0 |
| policy_clean@final | 1.0 | 1.0 |
| requirement_coverage@final | 1.0 | 1.0 |
| all_checks_passed@1 | 0.25 | 0.0 |
| all_checks_passed@final | 0.833 | 0.583 |
| avg_attempts | 2.17 | 2.58 |
| avg_latency_s | 20.2 | 14.6 |
| medium_findings_final | 0 | 1 |

First-attempt failures (fixed by the loop or not):
- `g02` errors=['Missing required argument: The argument "subnet_ids" is required, but no definition was found. (line 180)', 'Unsupported argument: An argument named "subnets" is not expected here. (line 182)'] policy=[] req=['network=private'] -> final passed=True
- `g03` errors=['HCL syntax error: Error: Invalid multi-line string\n\n  on <stdin> line 32, in resource "aws_cloudfront_origin_access_control" "oac":\n  32:   description"                       = "OAC for static marketing site S3 bucket"\n  33:   origin_access_control_origin_type = "s3"\n\nQuoted strings may not be split over multiple lines. To produce a multi-line\nstring, either use the \\n escape to represent a newline character or use the\n"heredoc" multi-line template syntax.\n\n\nError: Invalid string literal\n\n  on <stdin> line 32, in resource "aws_cloudfront_origin_access_control" "oac":\n  32:   description"                       = "OAC for static marketing site S3 bucket"\n  33:   origin_access_control_origin_type = "s3"\n\nThis item is not valid in a string literal.\n\n\nError: Invalid multi-line string\n\n  on <stdin> line 33, in resource "aws_cloudfront_origin_access_control" "oac":\n  33:   origin_access_control_origin_type = "s3"\n  34:   signing_behavior                  = "specifystore"\n\nQuoted strings may not be split over multiple lines. To produce a multi-line\nstring, either use the \\n escape to represent a newline character or use the\n"heredoc" multi-line template syntax.\n\n\nError: Invalid string literal\n\n  on <stdin> line 33, in resource "aws_cloudfront_origin_access_control" "oac":\n  33:   origin_access_control_origin_type = "s3"\n  34:   signing_behavior                  = "specifystore"\n\nThis item is not valid in a string literal.\n\n\nError: Invalid multi-line string\n\n  on <stdin> line 34, in resource "aws_cloud'] policy=[] req=[] -> final passed=True
- `g04` errors=["Missing required provider: This configuration requires provider registry.terraform.io/hashicorp/archive, but that provider isn't available. You may be able to install it automatically by running:\n  terraform init"] policy=[] req=[] -> final passed=False
- `g06` errors=['Missing required argument: The argument "subnet_ids" is required, but no definition was found. (line 251)', 'Unsupported argument: An argument named "subnets" is not expected here. (line 253)'] policy=[] req=[] -> final passed=False
- `g07` errors=[] policy=[] req=['network=private'] -> final passed=True
- `g08` errors=['HCL syntax error: Error: Attribute redefined\n\n  on <stdin> line 225, in resource "aws_autoscaling_group" "api":\n 225:     id      = aws_launch_template.api.id\n\nThe argument "id" was already set at <stdin>:224,5-7. Each argument may be\nset only once.'] policy=[] req=[] -> final passed=True
- `g09` errors=['Unsupported argument: An argument named "ignore_public_ccls" is not expected here. Did you mean "ignore_public_acls"? (line 208)'] policy=[] req=[] -> final passed=True
- `g10` errors=['HCL syntax error: Error: Attribute redefined\n\n  on <stdin> line 214, in resource "aws_autoscaling_group" "main":\n 214:     id      = aws_launch_template.main.id\n\nThe argument "id" was already set at <stdin>:213,5-7. Each argument may be\nset only once.'] policy=[] req=[] -> final passed=True
- `g12` errors=['Reference to undeclared resource: A managed resource "launch_template" "backend" has not been declared in the root module. (line 244)'] policy=[] req=[] -> final passed=True

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

| Metric | Value |
|---|---|
| faithfulness | 5.0 |
| coverage | 5.0 |
| clarity | 5.0 |
| share_faithfulness>=4 | 1.0 |
| explanations_with_hallucinated_resources | 0 |

Lowest-scored:
- `g01` faithfulness=5: The response accurately maps all provided requirements to concrete Terraform resources from the configuration. It provides a clear architectural overview, an accurate validation summary, and a realistic cost estimate well within the stated budget.
- `g02` faithfulness=5: The response accurately maps all provided requirements to the concrete Terraform resources in the configuration. The validation summary correctly reflects the tool output and the cost estimate is realistic for the configured AWS resources.
- `g03` faithfulness=5: The response accurately maps all user requirements to the specific Terraform resources provided in the configuration. It cleanly presents the validation results and provides a realistic cost estimate well within the specified budget.

## 8. Advisor (question path)

| Metric | Value |
|---|---|
| faithfulness | 2.5 |
| coverage | 4.0 |
| clarity | 4.25 |
| answers_with_citation | 0.917 |
| invalid_citation_rate | 0.0 |

Lowest-scored:
- `q02` faithfulness=2: While the response notes the gap in the provided documentation and accurately cites Amazon RDS Proxy from reference [3], it introduces unsupported external claims including the PostgreSQL max_connections parameter, specific sizing formulas, and CloudWatch connection metrics not found in the references, capping faithfulness at 2. Coverage and clarity are strong due to the structured list, though the response misses the non-production benchmarking guidance from reference [3] and includes a logical error by adding rather than subtracting a safety margin in its memory formula.
- `q03` faithfulness=2: Faithfulness is capped at 2 because the response makes the unsupported claim that RDS storage encryption supports compliance requirements and conflates DynamoDB key types from [4] with RDS. Coverage and clarity are 4 because the response concisely addresses data-at-rest protection, encrypted backups, and automated checks, but omits KMS key management benefits from [5] and awkwardly frames Terraform configuration syntax as a reason to enable encryption.
- `q04` faithfulness=2: Faithfulness is capped at 2 because the second paragraph makes specific unsupported claims about synchronous replication to a standby instance and automatic failover that do not appear in the provided reference excerpts. While the response directly answers the question clearly and concisely, it fails to acknowledge that the provided documentation only mentions multi_az in the context of RDS Custom for Oracle not supporting it.
