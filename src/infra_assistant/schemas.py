"""Pydantic schemas for structured LLM outputs and domain objects."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["TERRAFORM_REQUEST", "INFRA_QUESTION", "OUT_OF_SCOPE"]
NetworkAccess = Literal["public", "private", "public_frontend_private_backend"]
Availability = Literal["single_az", "multi_az"]
Database = Literal["none", "postgres", "mysql", "dynamodb"]
Compute = Literal["ec2", "ecs_fargate", "lambda", "static_site"]

# Slots the collector must fill (explicitly or after the follow-up budget, by assumption).
REQUIRED_SLOTS: tuple[str, ...] = (
    "workload",
    "traffic",
    "network_access",
    "availability",
    "database",
    "monthly_budget_usd",
)

# Defaults used when the user never answers - recorded as *assumptions*, not facts.
# `compute` has no default on purpose: the generator picks it from the workload.
SLOT_DEFAULTS: dict[str, object] = {
    "workload": "generic web application",
    "traffic": "low (under 100 requests/second)",
    "network_access": "public_frontend_private_backend",
    "availability": "single_az",
    "database": "none",
    "monthly_budget_usd": 100,
    "object_storage": False,
    "region": "eu-central-1",
}


class IntentResult(BaseModel):
    """Intent Router output."""

    intent: Intent = Field(
        description=(
            "TERRAFORM_REQUEST: user wants infrastructure built/changed or Terraform code. "
            "INFRA_QUESTION: user asks a conceptual/how-to question about cloud, AWS, databases, "
            "DevOps or Terraform without asking to generate a full config. "
            "OUT_OF_SCOPE: anything else."
        )
    )
    reason: str = Field(description="One short sentence justifying the label.")


class InfraRequirements(BaseModel):
    """Slots describing a small AWS workload. None = unknown."""

    workload: str | None = Field(None, description="What the system is, e.g. 'Django web shop', 'REST API for mobile app'.")
    traffic: str | None = Field(None, description="Expected load, e.g. '~200 req/s peak', '5k daily users'.")
    compute: Compute | None = Field(None, description="Compute platform if the user stated or clearly implied it.")
    network_access: NetworkAccess | None = Field(None, description="Who can reach the system.")
    availability: Availability | None = Field(None, description="multi_az if user needs HA / no downtime / production-critical; single_az for dev/test/cheap.")
    database: Database | None = Field(None, description="Database engine, 'none' if explicitly not needed.")
    storage_gb: int | None = Field(None, description="Database or file storage size in GB if mentioned.")
    object_storage: bool | None = Field(None, description="True if user needs S3 / file uploads / static assets bucket.")
    monthly_budget_usd: int | None = Field(None, description="Monthly cost ceiling in USD.")
    region: str | None = Field(None, description="AWS region code if mentioned, e.g. eu-central-1.")


class CollectorResult(BaseModel):
    """Requirements Collector output: updated slots + a follow-up question."""

    requirements: InfraRequirements
    follow_up_question: str = Field(
        "",
        description="One friendly question asking ONLY for the still-missing required slots. Empty if none missing.",
    )


class TerraformDraft(BaseModel):
    """Generator output."""

    hcl: str = Field(description="Complete Terraform HCL (no terraform{} or provider{} blocks).")
    design_notes: str = Field(description="3-6 bullet points on key design decisions.")


class JudgeScore(BaseModel):
    """LLM-as-judge rubric output (used by evals)."""

    faithfulness: int = Field(ge=1, le=5, description="Every claim is supported by the provided config/context.")
    coverage: int = Field(ge=1, le=5, description="Addresses all user requirements / the whole question.")
    clarity: int = Field(ge=1, le=5, description="Clear, well-structured, actionable.")
    rationale: str = Field(description="Two sentences explaining the scores, citing concrete issues.")


def missing_slots(req: InfraRequirements) -> list[str]:
    return [slot for slot in REQUIRED_SLOTS if getattr(req, slot) in (None, "")]


def merge_requirements(old: InfraRequirements, new: InfraRequirements) -> InfraRequirements:
    """New non-null values override old ones (user may change their mind)."""

    merged = old.model_dump()
    merged.update({k: v for k, v in new.model_dump().items() if v not in (None, "")})
    return InfraRequirements(**merged)


def apply_defaults(req: InfraRequirements) -> tuple[InfraRequirements, list[str]]:
    """Fill every unknown slot with a default and report which ones were assumed."""

    data = req.model_dump()
    assumed: list[str] = []
    for slot, default in SLOT_DEFAULTS.items():
        if data.get(slot) in (None, ""):
            data[slot] = default
            assumed.append(slot)
    return InfraRequirements(**data), assumed
