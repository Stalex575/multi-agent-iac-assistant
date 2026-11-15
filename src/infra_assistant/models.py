"""Gemini model factories (all model names come from Settings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.rate_limiters import InMemoryRateLimiter

from infra_assistant.config import Settings


@dataclass(frozen=True, slots=True)
class ModelBundle:
    """Chat models by role. Tests inject fakes with the same interface."""

    router: Any  # fast, temperature 0 - intent classification
    collector: Any  # fast, temperature 0 - slot extraction
    generator: Any  # heavy - Terraform generation
    writer: Any  # heavy - advisor answers + explanations
    judge: Any  # fast, temperature 0 - LLM-as-judge (evals only)
    embeddings: Any


def build_embedder(settings: Settings) -> Any:
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(
        model=settings.embedding_model,
        output_dimensionality=settings.embedding_dimension,
    )


def build_models(settings: Settings) -> ModelBundle:
    from langchain_google_genai import ChatGoogleGenerativeAI

    # One shared client-side limiter keeps every role under the free-tier RPM quota.
    limiter = InMemoryRateLimiter(requests_per_second=settings.llm_rpm / 60, check_every_n_seconds=0.2, max_bucket_size=2)

    def chat(model: str, temperature: float) -> ChatGoogleGenerativeAI:
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            rate_limiter=limiter,
        )

    return ModelBundle(
        router=chat(settings.fast_model, 0.0),
        collector=chat(settings.fast_model, 0.0),
        generator=chat(settings.heavy_model, 0.2),
        writer=chat(settings.heavy_model, 0.3),
        judge=chat(settings.fast_model, 0.0),
        embeddings=build_embedder(settings),
    )
