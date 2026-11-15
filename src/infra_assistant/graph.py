"""LangGraph wiring. Routing functions are pure and unit-tested."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from infra_assistant.config import Settings
from infra_assistant.models import ModelBundle, build_models
from infra_assistant.nodes import Nodes
from infra_assistant.state import AssistantState
from infra_assistant.store import KnowledgeIndex, Retriever, SemanticCache


def build_graph(nodes: Nodes, max_fix_retries: int, checkpointer: Any | None = None) -> Any:
    g = StateGraph(AssistantState)
    g.add_node("intent_router", nodes.intent_router)
    g.add_node("out_of_scope", nodes.out_of_scope)
    g.add_node("requirements_collector", nodes.requirements_collector)
    g.add_node("retrieve_for_terraform", nodes.retrieve_for_terraform)
    g.add_node("terraform_generator", nodes.terraform_generator)
    g.add_node("validator", nodes.validator)
    g.add_node("explainer", nodes.explainer)
    g.add_node("semantic_cache", nodes.semantic_cache)
    g.add_node("retrieve_for_question", nodes.retrieve_for_question)
    g.add_node("advisor", nodes.advisor)
    g.add_node("cache_write", nodes.cache_write)
    g.add_node("finish", nodes.finish)

    g.add_edge(START, "intent_router")
    g.add_conditional_edges("intent_router", route_intent, {
        "TERRAFORM_REQUEST": "requirements_collector",
        "INFRA_QUESTION": "semantic_cache",
        "OUT_OF_SCOPE": "out_of_scope",
    })
    g.add_edge("out_of_scope", "finish")

    g.add_conditional_edges("requirements_collector", route_after_collector, {
        "ask_user": "finish", "generate": "retrieve_for_terraform"})
    g.add_edge("retrieve_for_terraform", "terraform_generator")
    g.add_edge("terraform_generator", "validator")
    g.add_conditional_edges("validator", lambda s: route_after_validator(s, max_fix_retries), {
        "fix": "terraform_generator", "explain": "explainer"})
    g.add_edge("explainer", "finish")

    g.add_conditional_edges("semantic_cache", route_after_cache, {"hit": "finish", "miss": "retrieve_for_question"})
    g.add_edge("retrieve_for_question", "advisor")
    g.add_edge("advisor", "cache_write")
    g.add_edge("cache_write", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def route_intent(state: AssistantState) -> str:
    return state.get("intent") or "OUT_OF_SCOPE"


def route_after_collector(state: AssistantState) -> str:
    return "ask_user" if state.get("awaiting_requirements") else "generate"


def route_after_validator(state: AssistantState, max_fix_retries: int) -> str:
    """Self-correction loop: failed validation goes back to the generator with the errors."""

    if state.get("validation", {}).get("passed"):
        return "explain"
    return "fix" if state.get("fix_attempts", 0) < max_fix_retries else "explain"


def route_after_cache(state: AssistantState) -> str:
    return "hit" if state.get("cache_hit") else "miss"


def build_app(settings: Settings | None = None, models: ModelBundle | None = None, use_cache: bool = True) -> Any:
    """Production wiring: Gemini models, local hybrid index, persisted semantic cache, in-memory threads."""

    settings = settings or Settings.load()
    models = models or build_models(settings)
    index = KnowledgeIndex.load(settings.index_dir)
    retriever = Retriever(index, models.embeddings, settings.retrieval_top_k)
    cache = SemanticCache(settings.index_dir / "semantic_cache.jsonl", models.embeddings,
                          settings.semantic_cache_threshold) if use_cache else None
    nodes = Nodes(settings=settings, models=models, retriever=retriever, cache=cache)
    return build_graph(nodes, settings.max_fix_retries, checkpointer=MemorySaver())
