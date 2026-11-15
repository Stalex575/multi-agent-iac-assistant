from __future__ import annotations

from pathlib import Path

from infra_assistant.schemas import InfraRequirements, apply_defaults, merge_requirements, missing_slots
from infra_assistant.terraform_tools import extract_hcl, strip_forbidden_blocks, validate_config
from tests.conftest import VALID_HCL

SANDBOX = Path(__file__).resolve().parents[1] / "terraform_sandbox"
REQ = InfraRequirements(database="postgres", availability="multi_az", network_access="public_frontend_private_backend")


def test_valid_config_passes() -> None:
    report = validate_config(VALID_HCL, REQ, SANDBOX)
    assert report.validate_ok and report.passed, report.as_dict()


def test_provider_blocks_are_stripped() -> None:
    code = 'terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws" }\n  }\n}\nprovider "aws" {\n  region = "us-east-1"\n}\n' + VALID_HCL
    cleaned, removed = strip_forbidden_blocks(code)
    assert removed and "provider" not in cleaned and "aws_vpc" in cleaned


def test_markdown_fences_extracted() -> None:
    assert extract_hcl("Here:\n```hcl\nresource \"a\" \"b\" {}\n```\nbye") == 'resource "a" "b" {}'


def test_detects_invalid_argument_and_policy() -> None:
    bad = VALID_HCL.replace("storage_encrypted           = true", "unknown_arg = 1")
    report = validate_config(bad, REQ, SANDBOX)
    assert not report.validate_ok
    assert any(f.rule == "DB_ENCRYPTION" for f in report.policy)


def test_detects_requirement_violation() -> None:
    single = VALID_HCL.replace("multi_az                    = true", "multi_az = false")
    report = validate_config(single, REQ, SANDBOX)
    assert report.validate_ok and not report.requirements_ok


def test_open_ssh_is_flagged() -> None:
    sg = VALID_HCL + '''
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
    report = validate_config(sg, REQ, SANDBOX)
    assert any(f.rule == "SG_ADMIN_OPEN" for f in report.policy) and not report.passed


def test_slot_helpers() -> None:
    req = merge_requirements(InfraRequirements(database="mysql"), InfraRequirements(database="postgres", workload="api"))
    assert req.database == "postgres" and "traffic" in missing_slots(req)
    filled, assumed = apply_defaults(req)
    assert not missing_slots(filled) and "traffic" in assumed and "database" not in assumed
