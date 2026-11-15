"""Deterministic Terraform tooling: sanitize, fmt, validate, policy + requirement checks.

This is the "Validator" component. Everything here is plain code (no LLM), so the
validity metric it produces is objective and reproducible offline: the AWS provider
is pre-installed in `terraform_sandbox/.terraform` and every run re-uses it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

import hcl2

from infra_assistant.schemas import InfraRequirements

TERRAFORM_BIN = shutil.which("terraform") or str(Path.home() / ".local" / "bin" / "terraform")
ADMIN_PORTS = (22, 3389, 3306, 5432, 6379, 27017)
DB_RESOURCES = ("aws_db_instance", "aws_rds_cluster")
_FORBIDDEN_TOP_BLOCK = re.compile(r'^\s*(terraform\s*\{|provider\s+"[^"]+"\s*\{|variable\s+"aws_region"\s*\{)')


@dataclass
class Finding:
    rule: str
    severity: str  # HIGH | MEDIUM | LOW
    resource: str
    message: str


@dataclass
class ValidationReport:
    syntax_ok: bool = False
    validate_ok: bool = False
    errors: list[str] = field(default_factory=list)
    policy: list[Finding] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)  # {check, ok, message}
    sanitized: bool = False
    formatted_code: str = ""

    @property
    def high_findings(self) -> list[Finding]:
        return [f for f in self.policy if f.severity == "HIGH"]

    @property
    def requirements_ok(self) -> bool:
        return all(r["ok"] for r in self.requirements)

    @property
    def passed(self) -> bool:
        return self.validate_ok and not self.high_findings and self.requirements_ok

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(passed=self.passed, requirements_ok=self.requirements_ok)
        return data


# --------------------------------------------------------------------------- sanitize


def extract_hcl(text: str) -> str:
    """Pull HCL out of a markdown answer if the model wrapped it in fences."""

    if "\n" not in text.strip() and "\\n" in text:
        # Seen with Gemini structured output: the whole file comes back on one line with escaped newlines.
        text = text.replace("\\n", "\n").replace('\\"', '"')
    blocks = re.findall(r"```(?:hcl|terraform|tf)?\s*\n(.*?)```", text, flags=re.S)
    return "\n\n".join(b.strip() for b in blocks) if blocks else text.strip()


def strip_forbidden_blocks(code: str) -> tuple[str, bool]:
    """Remove top-level terraform{}, provider{} and variable "aws_region" blocks.

    They are owned by `terraform_sandbox/versions.tf`; duplicates would break
    validation for reasons unrelated to the generated infrastructure.
    """

    out: list[str] = []
    lines = code.splitlines()
    i, removed = 0, False
    while i < len(lines):
        if _FORBIDDEN_TOP_BLOCK.match(lines[i]):
            depth = 0
            while i < len(lines):
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
                if depth <= 0:
                    break
            removed = True
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip() + "\n", removed


# --------------------------------------------------------------------------- terraform CLI


def _run(args: list[str], cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [TERRAFORM_BIN, *args], cwd=cwd, input=stdin, capture_output=True, text=True, timeout=120
    )


def terraform_fmt(code: str, cwd: Path) -> tuple[bool, str, str]:
    """Returns (syntax_ok, formatted_code, error). fmt fails on any HCL syntax error."""

    proc = _run(["fmt", "-no-color", "-"], cwd=cwd, stdin=code)
    if proc.returncode != 0:
        return False, code, proc.stderr.strip()[:1500]
    return True, proc.stdout, ""


def terraform_validate(code: str, sandbox: Path, region: str = "eu-central-1") -> tuple[bool, list[str]]:
    """Run `terraform validate -json` in a throw-away dir that re-uses the cached provider."""

    run_dir = sandbox / "runs" / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True)
    try:
        (run_dir / ".terraform").symlink_to((sandbox / ".terraform").resolve(), target_is_directory=True)
        shutil.copy(sandbox / ".terraform.lock.hcl", run_dir / ".terraform.lock.hcl")
        shutil.copy(sandbox / "versions.tf", run_dir / "versions.tf")
        (run_dir / "region.tf").write_text(
            f'variable "aws_region" {{\n  type    = string\n  default = "{region}"\n}}\n'
        )
        (run_dir / "main.tf").write_text(code)
        proc = _run(["validate", "-json", "-no-color"], cwd=run_dir)
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return False, [proc.stderr.strip()[:1500] or "terraform validate produced no JSON"]
        errors = [
            f"{d.get('summary', '')}: {d.get('detail', '')}".strip(": ")
            + (f" (line {d['range']['start']['line']})" if d.get("range") else "")
            for d in result.get("diagnostics", [])
            if d.get("severity") == "error"
        ]
        return bool(result.get("valid")), errors
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


# --------------------------------------------------------------------------- HCL parsing


def _unquote(value: Any) -> Any:
    if isinstance(value, str):
        return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value
    if isinstance(value, list):
        return [_unquote(v) for v in value]
    if isinstance(value, dict):
        return {_unquote(k): _unquote(v) for k, v in value.items() if k != "__is_block__"}
    return value


def parse_resources(code: str) -> list[tuple[str, str, dict[str, Any]]]:
    """[(type, name, body)] for every `resource` block; [] if unparsable."""

    try:
        parsed = _unquote(hcl2.loads(code))
    except Exception:
        return []
    resources: list[tuple[str, str, dict[str, Any]]] = []
    for block in parsed.get("resource", []):
        for rtype, named in block.items():
            for name, body in named.items():
                resources.append((rtype, name, body if isinstance(body, dict) else {}))
    return resources


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _multiplicity(body: dict[str, Any]) -> int:
    """How many instances a resource block creates (dynamic count => assume 2+)."""

    for key in ("count", "for_each"):
        if key in body:
            value = body[key]
            if isinstance(value, int):
                return value
            if isinstance(value, (list, dict)):
                return len(value)
            return 2
    return 1


def _blocks(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for v in value:
            if isinstance(v, dict):
                yield v


# --------------------------------------------------------------------------- policy rules


def policy_findings(resources: list[tuple[str, str, dict[str, Any]]]) -> list[Finding]:
    """Security/reliability baseline derived from AWS Well-Architected best practices."""

    findings: list[Finding] = []
    types = {r[0] for r in resources}
    for rtype, name, body in resources:
        ref = f"{rtype}.{name}"
        if rtype in DB_RESOURCES:
            if not _is_true(body.get("storage_encrypted")):
                findings.append(Finding("DB_ENCRYPTION", "HIGH", ref, "set storage_encrypted = true (SEC08-BP02)"))
            if _is_true(body.get("publicly_accessible")):
                findings.append(Finding("DB_PUBLIC", "HIGH", ref, "database must not be publicly_accessible"))
            if int(_num(body.get("backup_retention_period"), 0)) < 1:
                findings.append(Finding("DB_BACKUP", "MEDIUM", ref, "set backup_retention_period >= 1 (REL09-BP01)"))
        if rtype == "aws_s3_bucket" and "aws_s3_bucket_public_access_block" not in types:
            findings.append(Finding("S3_PUBLIC_ACCESS", "HIGH", ref, "add aws_s3_bucket_public_access_block blocking public access"))
        if rtype == "aws_security_group":
            for rule in _blocks(body.get("ingress")):
                findings += _open_port_findings(ref, rule, rule.get("cidr_blocks") or [])
        if rtype == "aws_vpc_security_group_ingress_rule":
            findings += _open_port_findings(ref, body, [body.get("cidr_ipv4", "")])
        if rtype == "aws_instance" and not any(_is_true(b.get("encrypted")) for b in _blocks(body.get("root_block_device"))):
            findings.append(Finding("EBS_ENCRYPTION", "MEDIUM", ref, "encrypt root_block_device"))
    return findings


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _open_port_findings(ref: str, rule: dict[str, Any], cidrs: list[Any]) -> list[Finding]:
    if "0.0.0.0/0" not in [str(c) for c in cidrs]:
        return []
    low, high = _num(rule.get("from_port"), 0), _num(rule.get("to_port"), 65535)
    if str(rule.get("ip_protocol", rule.get("protocol", ""))) == "-1":
        low, high = 0, 65535
    exposed = [p for p in ADMIN_PORTS if low <= p <= high]
    if not exposed:
        return []
    return [Finding("SG_ADMIN_OPEN", "HIGH", ref, f"ports {exposed} open to 0.0.0.0/0")]


# --------------------------------------------------------------------------- requirement checks


def requirement_checks(resources: list[tuple[str, str, dict[str, Any]]], req: InfraRequirements) -> list[dict[str, Any]]:
    """Does the config actually implement what the user asked for?"""

    by_type: dict[str, list[dict[str, Any]]] = {}
    for rtype, _, body in resources:
        by_type.setdefault(rtype, []).append(body)
    checks: list[dict[str, Any]] = []

    def add(check: str, ok: bool, message: str) -> None:
        checks.append({"check": check, "ok": ok, "message": "ok" if ok else message})

    dbs = by_type.get("aws_db_instance", []) + by_type.get("aws_rds_cluster", [])
    engines = " ".join(str(b.get("engine", "")) for b in dbs)
    if req.database in ("postgres", "mysql"):
        add(f"database={req.database}", req.database in engines, f"no RDS/Aurora resource with {req.database} engine")
    elif req.database == "dynamodb":
        add("database=dynamodb", "aws_dynamodb_table" in by_type, "no aws_dynamodb_table")
    elif req.database == "none":
        add("database=none", not dbs and "aws_dynamodb_table" not in by_type, "a database was created but user needs none")

    if req.availability == "multi_az":
        subnets = sum(_multiplicity(b) for b in by_type.get("aws_subnet", []))
        uses_vpc = any(t in by_type for t in ("aws_instance", "aws_autoscaling_group", "aws_ecs_service", "aws_lb", *DB_RESOURCES))
        add("multi_az.subnets", not uses_vpc or subnets >= 2, "need subnets in >= 2 AZs")
        if req.database in ("postgres", "mysql"):
            ha_db = any(_is_true(b.get("multi_az")) for b in by_type.get("aws_db_instance", [])) or (
                "aws_rds_cluster" in by_type and sum(_multiplicity(b) for b in by_type.get("aws_rds_cluster_instance", [])) >= 2
            )
            add("multi_az.database", ha_db, "database must be multi_az = true (or Aurora with >= 2 instances)")

    compute_markers = {
        "ec2": ("aws_instance", "aws_launch_template", "aws_autoscaling_group"),
        "ecs_fargate": ("aws_ecs_service",),
        "lambda": ("aws_lambda_function",),
        "static_site": ("aws_cloudfront_distribution",),
    }
    if req.compute in compute_markers:
        add(f"compute={req.compute}", any(t in by_type for t in compute_markers[req.compute]), f"no {req.compute} resources")

    if req.object_storage:
        add("object_storage", "aws_s3_bucket" in by_type, "user needs object storage but no aws_s3_bucket")

    lbs = by_type.get("aws_lb", [])
    if req.network_access == "private":
        add("network=private", all(_is_true(b.get("internal")) for b in lbs), "load balancer must be internal = true")
    elif req.network_access in ("public", "public_frontend_private_backend"):
        entry = (
            any(not _is_true(b.get("internal")) for b in lbs)
            or any(t in by_type for t in ("aws_cloudfront_distribution", "aws_apigatewayv2_api", "aws_lambda_function_url", "aws_eip"))
            or any(_is_true(b.get("associate_public_ip_address")) for b in by_type.get("aws_instance", []))
        )
        add(f"network={req.network_access}", entry, "no internet-facing entry point (public ALB/CloudFront/API GW/EIP)")
    return checks


# --------------------------------------------------------------------------- entry point


def validate_config(raw_code: str, req: InfraRequirements | None, sandbox: Path) -> ValidationReport:
    report = ValidationReport()
    code, report.sanitized = strip_forbidden_blocks(extract_hcl(raw_code))
    report.syntax_ok, code, fmt_error = terraform_fmt(code, sandbox)
    report.formatted_code = code
    if not report.syntax_ok:
        report.errors = [f"HCL syntax error: {fmt_error}"]
        return report
    region = (req.region if req and req.region else "eu-central-1")
    report.validate_ok, report.errors = terraform_validate(code, sandbox, region)
    resources = parse_resources(code)
    report.policy = policy_findings(resources)
    if req is not None:
        report.requirements = requirement_checks(resources, req)
    return report
