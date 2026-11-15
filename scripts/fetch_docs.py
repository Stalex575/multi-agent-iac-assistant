"""CRISP-DM Data Understanding: download the real knowledge-base documents.

Sources (both public, no API key required):
1. AWS Well-Architected Framework best-practice pages (Reliability, Security,
   Cost optimization, Performance efficiency pillars) - "best practices docs".
2. Terraform AWS provider resource docs (hashicorp/terraform-provider-aws) -
   syntax/argument reference so generated HCL is valid.

The script is idempotent and resumable: files already on disk are skipped, so it
can be re-run after a network drop.

Usage:  python scripts/fetch_docs.py [--max-wa 400]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
WA_BASE = "https://docs.aws.amazon.com/wellarchitected/latest/framework/"
TF_BASE = "https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/"
WA_PILLARS = {"Reliability", "Security", "Cost optimization", "Performance efficiency"}

# Resources a "small AWS service" typically needs (web app / API / DB / storage / network).
TF_RESOURCES = [
    "vpc", "subnet", "internet_gateway", "nat_gateway", "eip", "route_table",
    "route_table_association", "security_group", "vpc_security_group_ingress_rule",
    "vpc_security_group_egress_rule", "instance", "launch_template", "autoscaling_group",
    "autoscaling_policy", "lb", "lb_target_group", "lb_listener", "db_instance",
    "db_subnet_group", "rds_cluster", "rds_cluster_instance", "dynamodb_table",
    "elasticache_cluster", "elasticache_replication_group", "s3_bucket",
    "s3_bucket_public_access_block", "s3_bucket_server_side_encryption_configuration",
    "s3_bucket_versioning", "s3_bucket_lifecycle_configuration", "cloudfront_distribution",
    "ecs_cluster", "ecs_service", "ecs_task_definition", "ecr_repository", "lambda_function",
    "apigatewayv2_api", "apigatewayv2_stage", "apigatewayv2_integration", "apigatewayv2_route",
    "iam_role", "iam_role_policy_attachment", "iam_instance_profile", "kms_key",
    "cloudwatch_metric_alarm", "cloudwatch_log_group", "sqs_queue", "sns_topic",
    "secretsmanager_secret", "efs_file_system", "backup_plan", "wafv2_web_acl",
    "acm_certificate", "route53_record",
]


def _get(url: str, retries: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nlp-hw-docs-fetcher/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as exc:  # network is flaky; back off and retry
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")


def wa_leaves() -> list[dict[str, str]]:
    toc = json.loads(_get(WA_BASE + "toc-contents.json"))
    leaves: list[dict[str, str]] = []

    def walk(node: dict, path: list[str]) -> None:
        for child in node.get("contents", []):
            p = path + [child["title"]]
            if "contents" in child:
                walk(child, p)
            elif len(p) > 1 and p[0].startswith("Appendix") and p[1] in WA_PILLARS:
                leaves.append({"title": p[-1], "pillar": p[1], "href": child["href"]})

    walk(toc, [])
    return leaves


def fetch_wa(max_pages: int) -> int:
    out = RAW / "well_architected"
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for leaf in wa_leaves()[:max_pages]:
        target = out / (leaf["href"].replace(".html", ".json"))
        if target.exists():
            count += 1
            continue
        try:
            html = _get(WA_BASE + leaf["href"])
        except RuntimeError as exc:
            print(f"skip {leaf['href']}: {exc}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        body = soup.find(id="main-col-body") or soup.find("main") or soup
        text = body.get_text("\n", strip=True)
        target.write_text(
            json.dumps({**leaf, "url": WA_BASE + leaf["href"], "text": text}, ensure_ascii=False),
            encoding="utf-8",
        )
        count += 1
    return count


def fetch_tf() -> int:
    out = RAW / "terraform_aws"
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for name in TF_RESOURCES:
        target = out / f"aws_{name}.md"
        if target.exists():
            count += 1
            continue
        try:
            target.write_bytes(_get(f"{TF_BASE}{name}.html.markdown"))
            count += 1
        except RuntimeError as exc:
            print(f"skip aws_{name}: {exc}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-wa", type=int, default=400)
    args = parser.parse_args()
    print(f"terraform docs: {fetch_tf()}")
    print(f"well-architected pages: {fetch_wa(args.max_wa)}")


if __name__ == "__main__":
    main()
