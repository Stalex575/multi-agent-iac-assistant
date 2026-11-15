"""Runtime configuration.

Values come from `.env` (python-dotenv) with OS environment as fallback. Only
GOOGLE_API_KEY is required; everything else has a sensible default so the
system is runnable with a single key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


class SettingsError(ValueError):
    """Raised when required runtime settings are missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    google_api_key: str
    heavy_model: str
    fast_model: str
    judge_model: str
    embedding_model: str
    embedding_dimension: int
    semantic_cache_threshold: float
    max_fix_retries: int
    max_followup_turns: int
    retrieval_top_k: int
    llm_timeout_seconds: int
    llm_max_retries: int
    llm_rpm: int
    judge_rpm: int
    index_dir: Path
    sandbox_dir: Path

    @classmethod
    def load(cls, env_path: str | Path = DEFAULT_ENV_PATH) -> "Settings":
        """`.env` is the base; an exported variable overrides it (handy for one-off eval runs)."""

        path = Path(env_path)
        raw: dict[str, str | None] = dict(dotenv_values(path)) if path.exists() else {}
        # LangSmith reads its settings from the process environment, so pass the tracing keys on.
        for key, value in raw.items():
            if key.startswith(("LANGSMITH_", "LANGCHAIN_")) and value:
                os.environ.setdefault(key, value)
        raw.update(os.environ)
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, str | None]) -> "Settings":
        settings = cls(
            google_api_key=_required(raw, "GOOGLE_API_KEY"),
            heavy_model=_get(raw, "GEMINI_HEAVY_MODEL", "gemini-3.5-flash-lite"),
            fast_model=_get(raw, "GEMINI_FAST_MODEL", "gemini-3.1-flash-lite"),
            judge_model=_get(raw, "GEMINI_JUDGE_MODEL", "gemini-3.8-flash"),
            embedding_model=_get(raw, "GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"),
            embedding_dimension=int(_get(raw, "GEMINI_EMBEDDING_DIMENSION", "768")),
            semantic_cache_threshold=float(_get(raw, "SEMANTIC_CACHE_THRESHOLD", "0.94")),
            max_fix_retries=int(_get(raw, "MAX_FIX_RETRIES", "2")),
            max_followup_turns=int(_get(raw, "MAX_FOLLOWUP_TURNS", "3")),
            retrieval_top_k=int(_get(raw, "RETRIEVAL_TOP_K", "6")),
            llm_timeout_seconds=int(_get(raw, "LLM_TIMEOUT_SECONDS", "90")),
            llm_max_retries=int(_get(raw, "LLM_MAX_RETRIES", "3")),
            llm_rpm=int(_get(raw, "LLM_RPM", "14")),
            judge_rpm=int(_get(raw, "JUDGE_RPM", "4")),
            index_dir=Path(_get(raw, "INDEX_DIR", str(PROJECT_ROOT / "data" / "index"))),
            sandbox_dir=Path(_get(raw, "SANDBOX_DIR", str(PROJECT_ROOT / "terraform_sandbox"))),
        )
        settings.validate()
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key
        return settings

    def validate(self) -> None:
        if not 0.0 <= self.semantic_cache_threshold <= 1.0:
            raise SettingsError("SEMANTIC_CACHE_THRESHOLD must be between 0 and 1")
        if self.max_fix_retries < 0 or self.max_followup_turns < 0:
            raise SettingsError("retry/turn budgets must be >= 0")
        if self.retrieval_top_k < 1 or self.llm_rpm < 1 or self.judge_rpm < 1:
            raise SettingsError("RETRIEVAL_TOP_K, LLM_RPM and JUDGE_RPM must be >= 1")


def _required(raw: Mapping[str, str | None], key: str) -> str:
    value = raw.get(key)
    if value is None or not value.strip() or value.startswith("replace-with"):
        raise SettingsError(f"Missing required setting {key} (set it in .env)")
    return value.strip()


def _get(raw: Mapping[str, str | None], key: str, default: str) -> str:
    value = raw.get(key)
    return default if value is None or not value.strip() else value.strip()
