"""LangGraph node implementations. Each node owns a small set of state keys."""

from __future__ import annotations

import asyncio
import functools
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from infra_assistant import prompts
from infra_assistant.config import Settings
from infra_assistant.models import ModelBundle
from infra_assistant.schemas import (
    CollectorResult,
    InfraRequirements,
    IntentResult,
    TerraformDraft,
    apply_defaults,
    merge_requirements,
    missing_slots,
    REQUIRED_SLOTS,
)
from infra_assistant.state import AssistantState
from infra_assistant.store import Retriever, SemanticCache, format_context
from infra_assistant.terraform_tools import ValidationReport, validate_config

OUT_OF_SCOPE_REPLY = (
    "I'm InfraPilot - I can design small AWS infrastructures, generate validated Terraform for them, "
    "and answer AWS/DevOps/database questions. That request is outside my scope. "
    "Try e.g. *\"I need to host a Flask API with Postgres, highly available, under $200/month\"*."
)

# Retrieval queries per requirement value: resource docs give syntax, WA pages give best practice.
COMPUTE_QUERIES = {
    "ec2": "aws_launch_template aws_autoscaling_group aws_lb_target_group EC2 instances behind application load balancer",
    "ecs_fargate": "aws_ecs_service aws_ecs_task_definition Fargate network_configuration load_balancer",
    "lambda": "aws_lambda_function aws_apigatewayv2_api HTTP API integration Lambda",
    "static_site": "aws_cloudfront_distribution S3 origin access control static website",
}
DATABASE_QUERIES = {
    "postgres": "aws_db_instance postgres multi_az storage_encrypted db_subnet_group manage_master_user_password",
    "mysql": "aws_db_instance mysql multi_az storage_encrypted db_subnet_group manage_master_user_password",
    "dynamodb": "aws_dynamodb_table billing_mode PAY_PER_REQUEST point_in_time_recovery",
}


def traced(name: str) -> Callable[..., Callable[..., Awaitable[dict[str, Any]]]]:
    """Append {node, ms, info} to the per-turn trace shown in the UI and used by evals."""

    def deco(fn: Callable[..., Awaitable[dict[str, Any]]]) -> Callable[..., Awaitable[dict[str, Any]]]:
        @functools.wraps(fn)
        async def wrapper(self: "Nodes", state: AssistantState) -> dict[str, Any]:
            start = time.perf_counter()
            update = await fn(self, state)
            info = update.pop("_info", {})
            step = {"node": name, "ms": int((time.perf_counter() - start) * 1000), **info}
            update["trace"] = list(state.get("trace", [])) + [step]
            return update

        return wrapper

    return deco


@dataclass(slots=True)
class Nodes:
    settings: Settings
    models: ModelBundle
    retriever: Retriever
    cache: SemanticCache | None

    # ------------------------------------------------------------------ routing
    @traced("intent_router")
    async def intent_router(self, state: AssistantState) -> dict[str, Any]:
        if state.get("awaiting_requirements"):
            # Mid-collection answers ("postgres, $200") are not classifiable on their own.
            return {"intent": "TERRAFORM_REQUEST", "intent_reason": "answering follow-up",
                    "_info": {"intent": "TERRAFORM_REQUEST", "skipped_llm": True}}
        llm = self.models.router.with_structured_output(IntentResult)
        result: IntentResult = await llm.ainvoke(
            [("system", prompts.ROUTER_SYSTEM), ("human", prompts.router_user(state["user_message"], state.get("history", [])))]
        )
        return {"intent": result.intent, "intent_reason": result.reason, "_info": {"intent": result.intent}}

    @traced("out_of_scope")
    async def out_of_scope(self, state: AssistantState) -> dict[str, Any]:
        return {"final_response": OUT_OF_SCOPE_REPLY}

    # ------------------------------------------------------------------ terraform path
    @traced("requirements_collector")
    async def requirements_collector(self, state: AssistantState) -> dict[str, Any]:
        current = InfraRequirements(**(state.get("requirements") or {}))
        llm = self.models.collector.with_structured_output(CollectorResult)
        system = prompts.COLLECTOR_SYSTEM.replace("{required}", ", ".join(REQUIRED_SLOTS))
        result: CollectorResult = await llm.ainvoke(
            [("system", system), ("human", prompts.collector_user(current.model_dump(), state.get("history", [])))]
        )
        merged = merge_requirements(current, result.requirements)
        missing = missing_slots(merged)
        turns = state.get("followup_turns", 0)
        if missing and turns < self.settings.max_followup_turns:
            question = result.follow_up_question.strip() or _fallback_question(missing)
            return {"requirements": merged.model_dump(), "awaiting_requirements": True, "followup_turns": turns + 1,
                    "final_response": question, "_info": {"missing": missing, "asked": True}}
        completed, assumed = apply_defaults(merged)
        return {"requirements": completed.model_dump(), "assumed_slots": assumed, "awaiting_requirements": False,
                "followup_turns": 0, "_info": {"missing": missing, "assumed": assumed, "asked": False}}

    @traced("retriever")
    async def retrieve_for_terraform(self, state: AssistantState) -> dict[str, Any]:
        queries = terraform_queries(InfraRequirements(**state["requirements"]))
        results = await asyncio.gather(*(self.retriever.search(q, k=3) for q in queries))
        hits = _dedupe([h for r in results for h in r])[: self.settings.retrieval_top_k + 4]
        return {"retrieved": hits, "_info": {"queries": queries, "docs": [h["doc_id"] for h in hits]}}

    @traced("terraform_generator")
    async def terraform_generator(self, state: AssistantState) -> dict[str, Any]:
        validation = state.get("validation") or {}
        attempt = state.get("fix_attempts", 0) + (1 if validation else 0)
        feedback = _feedback_from(validation) if validation else ""
        llm = self.models.generator.with_structured_output(TerraformDraft)
        draft: TerraformDraft = await llm.ainvoke([
            ("system", prompts.GENERATOR_SYSTEM),
            ("human", prompts.generator_user(state["requirements"], state.get("assumed_slots", []),
                                             format_context(state.get("retrieved", []), 1500), feedback,
                                             state.get("terraform_code", ""))),
        ])
        return {"terraform_code": draft.hcl, "design_notes": draft.design_notes, "fix_attempts": attempt,
                "_info": {"attempt": attempt, "lines": draft.hcl.count("\n") + 1, "fixing": bool(feedback)}}

    @traced("validator")
    async def validator(self, state: AssistantState) -> dict[str, Any]:
        req = InfraRequirements(**state["requirements"])
        report: ValidationReport = await asyncio.to_thread(
            validate_config, state["terraform_code"], req, self.settings.sandbox_dir
        )
        info = {"passed": report.passed, "validate_ok": report.validate_ok, "errors": len(report.errors),
                "high_policy": [f.rule for f in report.high_findings],
                "failed_requirements": [r["check"] for r in report.requirements if not r["ok"]]}
        return {"validation": report.as_dict(), "terraform_code": report.formatted_code, "_info": info}

    @traced("explainer")
    async def explainer(self, state: AssistantState) -> dict[str, Any]:
        validation = state.get("validation", {})
        summary = validation_summary(validation)
        response = await self.models.writer.ainvoke([
            ("system", prompts.EXPLAINER_SYSTEM),
            ("human", prompts.explainer_user(state["requirements"], state.get("assumed_slots", []),
                                             state["terraform_code"], summary)),
        ])
        explanation = message_text(response)
        status = "✅ passed all checks" if validation.get("passed") else "⚠️ did not pass all checks - review before use"
        final = f"{explanation}\n\n**Validator:** {status}\n\n```hcl\n{state['terraform_code'].strip()}\n```"
        return {"final_response": final, "_info": {"chars": len(explanation)}}

    # ------------------------------------------------------------------ question path
    @traced("semantic_cache")
    async def semantic_cache(self, state: AssistantState) -> dict[str, Any]:
        if self.cache is None:
            return {"cache_hit": False, "_info": {"enabled": False}}
        lookup = await self.cache.lookup(state["user_message"])
        update: dict[str, Any] = {"cache_hit": lookup.hit, "cache_score": lookup.score,
                                  "_info": {"hit": lookup.hit, "score": round(lookup.score, 4)}}
        if lookup.hit:
            update["final_response"] = lookup.response + f"\n\n_(served from semantic cache, similarity {lookup.score:.3f})_"
        return update

    @traced("retriever")
    async def retrieve_for_question(self, state: AssistantState) -> dict[str, Any]:
        hits = await self.retriever.search(state["user_message"])
        return {"retrieved": hits, "_info": {"docs": [h["doc_id"] for h in hits]}}

    @traced("advisor")
    async def advisor(self, state: AssistantState) -> dict[str, Any]:
        hits = state.get("retrieved", [])
        response = await self.models.writer.ainvoke([
            ("system", prompts.ADVISOR_SYSTEM),
            ("human", prompts.advisor_user(state["user_message"], format_context(hits))),
        ])
        answer = message_text(response)
        sources = "\n".join(f"[{n}] {h['title']} - {h['source']}" for n, h in enumerate(hits, 1))
        return {"final_response": f"{answer}\n\n**Sources**\n{sources}", "_info": {"chars": len(answer)}}

    @traced("cache_write")
    async def cache_write(self, state: AssistantState) -> dict[str, Any]:
        if self.cache is not None and state.get("final_response"):
            await self.cache.write(state["user_message"], state["final_response"])
        return {}

    # ------------------------------------------------------------------ common
    async def finish(self, state: AssistantState) -> dict[str, Any]:
        return {"history": [{"role": "assistant", "content": state.get("final_response", "")}]}


def terraform_queries(req: InfraRequirements) -> list[str]:
    queries = ["aws_vpc aws_subnet aws_security_group private public subnets"]
    queries.append(COMPUTE_QUERIES.get(req.compute or "ec2", COMPUTE_QUERIES["ec2"]))
    if req.database in DATABASE_QUERIES:
        queries.append(DATABASE_QUERIES[req.database])
    if req.object_storage:
        queries.append("aws_s3_bucket aws_s3_bucket_public_access_block server side encryption")
    if req.availability == "multi_az":
        queries.append("deploy the workload to multiple Availability Zones fault isolation")
    queries.append(f"cost optimization select resource type and size for budget {req.monthly_budget_usd} USD per month")
    return queries


def validation_summary(v: dict[str, Any]) -> str:
    if not v:
        return "not validated"
    lines = [f"terraform validate: {'OK' if v.get('validate_ok') else 'FAILED'}"]
    lines += [f"  error: {e}" for e in v.get("errors", [])[:5]]
    lines += [f"policy {f['severity']} {f['rule']} {f['resource']}: {f['message']}" for f in v.get("policy", [])]
    lines += [f"requirement {r['check']}: {r['message']}" for r in v.get("requirements", [])]
    return "\n".join(lines)


def _feedback_from(v: dict[str, Any]) -> str:
    lines = [f"- terraform: {e}" for e in v.get("errors", [])[:8]]
    lines += [f"- policy {f['rule']} on {f['resource']}: {f['message']}" for f in v.get("policy", []) if f["severity"] == "HIGH"]
    lines += [f"- requirement {r['check']}: {r['message']}" for r in v.get("requirements", []) if not r["ok"]]
    return "\n".join(lines)


def _fallback_question(missing: list[str]) -> str:
    return "To design this I still need: " + ", ".join(m.replace("_", " ") for m in missing) + ". Could you tell me?"


def _dedupe(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for h in hits:
        if h["id"] not in seen:
            seen.add(h["id"])
            out.append(h)
    return out


def message_text(response: object) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(b if isinstance(b, str) else str(b.get("text", "")) for b in content).strip()
    return str(content).strip()
