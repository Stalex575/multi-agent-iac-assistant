"""Validator: does it catch broken configs at the right stage? (offline, no LLM)

Labelled cases are built by mutating known-good configs. Each case has the expected
decision (pass/fail) and the stage that should catch it (syntax / validate / policy /
requirement). Metrics: decision accuracy, fail-detection precision/recall, stage accuracy.
"""

from __future__ import annotations

from typing import Any

from evals.common import prf, run_resumable
from infra_assistant.config import PROJECT_ROOT
from infra_assistant.schemas import InfraRequirements
from infra_assistant.terraform_tools import validate_config

NAME = "validator"
SANDBOX = PROJECT_ROOT / "terraform_sandbox"

RDS = '''
data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]
}

resource "aws_db_subnet_group" "db" {
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "db" {
  engine                      = "postgres"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  username                    = "app"
  manage_master_user_password = true
  multi_az                    = true
  storage_encrypted           = true
  publicly_accessible         = false
  backup_retention_period     = 7
  db_subnet_group_name        = aws_db_subnet_group.db.name
  skip_final_snapshot         = true
}

resource "aws_lb" "app" {
  name               = "app"
  load_balancer_type = "application"
  internal           = false
  subnets            = aws_subnet.private[*].id
}
'''

S3 = '''
resource "aws_s3_bucket" "site" {
  bucket = "my-static-site-example-123"
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
'''

LAMBDA = '''
resource "aws_iam_role" "fn" {
  name = "fn-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_lambda_function" "api" {
  function_name = "api"
  role          = aws_iam_role.fn.arn
  handler       = "app.handler"
  runtime       = "python3.12"
  filename      = "app.zip"
}

resource "aws_dynamodb_table" "items" {
  name         = "items"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
}

resource "aws_apigatewayv2_api" "http" {
  name          = "api"
  protocol_type = "HTTP"
}
'''

SSH_RULE = '''
resource "aws_security_group" "web" {
  vpc_id = aws_vpc.main.id
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
'''

PG_HA = {"database": "postgres", "availability": "multi_az", "network_access": "public_frontend_private_backend"}
STORAGE = {"database": "none", "network_access": "private", "availability": "single_az", "object_storage": True}
SERVERLESS = {"database": "dynamodb", "compute": "lambda", "network_access": "public", "availability": "single_az"}

CASES: list[dict[str, Any]] = [
    {"id": "v01", "code": RDS, "req": PG_HA, "expect": "pass"},
    {"id": "v02", "code": RDS.replace("allocated_storage           = 20", "allocated_storage = 20\n  storage_size_gb = 20"), "req": PG_HA, "expect": "validate"},
    {"id": "v03", "code": RDS.replace("aws_db_subnet_group.db.name", "aws_db_subnet_group.missing.name"), "req": PG_HA, "expect": "validate"},
    {"id": "v04", "code": RDS.replace('resource "aws_vpc" "main" {', 'resource "aws_vpc" "main" {{'), "req": PG_HA, "expect": "syntax"},
    {"id": "v05", "code": RDS.replace('allocated_storage           = 20', 'allocated_storage = "twenty"'), "req": PG_HA, "expect": "validate"},
    {"id": "v06", "code": RDS.replace("storage_encrypted           = true", "storage_encrypted = false"), "req": PG_HA, "expect": "policy"},
    {"id": "v07", "code": RDS.replace("publicly_accessible         = false", "publicly_accessible = true"), "req": PG_HA, "expect": "policy"},
    {"id": "v08", "code": RDS + SSH_RULE, "req": PG_HA, "expect": "policy"},
    {"id": "v09", "code": RDS.replace("multi_az                    = true", "multi_az = false"), "req": PG_HA, "expect": "requirement"},
    {"id": "v10", "code": RDS.replace('engine                      = "postgres"', 'engine = "mysql"'), "req": PG_HA, "expect": "requirement"},
    {"id": "v11", "code": RDS.replace("internal           = false", "internal = true"), "req": PG_HA, "expect": "requirement"},
    {"id": "v12", "code": RDS, "req": {**PG_HA, "network_access": "private"}, "expect": "requirement"},
    {"id": "v13", "code": RDS, "req": {**PG_HA, "database": "none"}, "expect": "requirement"},
    {"id": "v14", "code": S3, "req": STORAGE, "expect": "pass"},
    {"id": "v15", "code": S3.split('resource "aws_s3_bucket_public_access_block"')[0], "req": STORAGE, "expect": "policy"},
    {"id": "v16", "code": LAMBDA, "req": SERVERLESS, "expect": "pass"},
    {"id": "v17", "code": LAMBDA.split('resource "aws_dynamodb_table"')[0] + 'resource "aws_apigatewayv2_api" "http" {\n  name = "api"\n  protocol_type = "HTTP"\n}\n',
     "req": SERVERLESS, "expect": "requirement"},
    {"id": "v18", "code": 'terraform {\n  required_version = ">= 1.5"\n}\nprovider "aws" {\n  region = "us-east-1"\n}\n' + LAMBDA,
     "req": SERVERLESS, "expect": "pass"},
    {"id": "v19", "code": "Here is your config:\n```hcl\n" + S3 + "```\nGood luck!", "req": STORAGE, "expect": "pass"},
    {"id": "v21", "code": S3, "req": {**STORAGE, "network_access": "public"}, "expect": "requirement"},
    {"id": "v20", "code": LAMBDA.replace('runtime       = "python3.12"', 'runtime = "python3.12"\n  memory = 512'), "req": SERVERLESS, "expect": "validate"},
]


def caught_stage(v: dict[str, Any]) -> str:
    if not v["syntax_ok"]:
        return "syntax"
    if not v["validate_ok"]:
        return "validate"
    if any(f["severity"] == "HIGH" for f in v["policy"]):
        return "policy"
    if not v["requirements_ok"]:
        return "requirement"
    return "pass"


async def run(ctx: Any = None) -> list[dict[str, Any]]:
    async def one(case: dict[str, Any]) -> dict[str, Any]:
        report = validate_config(case["code"], InfraRequirements(**case["req"]), SANDBOX).as_dict()
        stage = caught_stage(report)
        return {"expect": case["expect"], "got": stage, "ok": stage == case["expect"],
                "decision_ok": (stage == "pass") == (case["expect"] == "pass"),
                "errors": report["errors"][:2], "policy": [f["rule"] for f in report["policy"]],
                "failed_requirements": [r["check"] for r in report["requirements"] if not r["ok"]]}

    return await run_resumable(NAME, CASES, one)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    tp = sum(r["expect"] != "pass" and r["got"] != "pass" for r in rows)
    fp = sum(r["expect"] == "pass" and r["got"] != "pass" for r in rows)
    fn = sum(r["expect"] != "pass" and r["got"] == "pass" for r in rows)
    return {
        "n": len(rows),
        "decision_accuracy": round(sum(r["decision_ok"] for r in rows) / len(rows), 3),
        "fail_detection": prf(tp, fp, fn),
        "stage_accuracy": round(sum(r["ok"] for r in rows) / len(rows), 3),
        "mistakes": [{"id": r["id"], "expect": r["expect"], "got": r["got"]} for r in rows if not r["ok"]],
    }
