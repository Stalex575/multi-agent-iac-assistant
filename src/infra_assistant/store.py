"""Local knowledge store: hybrid retrieval (BM25 + dense) and a semantic cache.

Replaces Pinecone from the original project: a ~2k-chunk corpus fits in memory,
needs no extra API key, and makes retrieval evals reproducible. Dense vectors come
from Gemini embeddings (computed once by `scripts/build_index.py`).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9_]+")
RRF_K = 60


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def query_text(q: str) -> str:
    return f"task: search result | query: {q.strip()}"


def document_text(title: str, text: str) -> str:
    return f"title: {title.strip() or 'none'} | text: {text.strip()}"


def similarity_text(q: str) -> str:
    return f"task: sentence similarity | query: {q.strip()}"


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.clip(norms, 1e-12, None)


@dataclass
class KnowledgeIndex:
    chunks: list[dict[str, Any]]  # {"id","doc_id","title","source","kind","text"}
    vectors: np.ndarray | None  # (n, d) L2-normalised, or None if not embedded
    _bm25: BM25Okapi = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._bm25 = BM25Okapi([tokenize(f"{c['title']} {c['text']}") for c in self.chunks])

    @classmethod
    def load(cls, index_dir: Path) -> "KnowledgeIndex":
        meta = index_dir / "chunks.jsonl"
        if not meta.exists():
            raise FileNotFoundError(f"{meta} missing - run `python scripts/build_index.py`")
        chunks = [json.loads(line) for line in meta.read_text(encoding="utf-8").splitlines() if line]
        vec_path = index_dir / "vectors.npy"
        vectors = _normalize(np.load(vec_path)) if vec_path.exists() else None
        if vectors is not None and len(vectors) != len(chunks):
            vectors = None  # stale embeddings; fall back to BM25 only
        return cls(chunks=chunks, vectors=vectors)

    def bm25_ranking(self, query: str, k: int) -> list[tuple[int, float]]:
        scores = self._bm25.get_scores(tokenize(query))
        order = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in order]

    def dense_ranking(self, query_vec: list[float] | np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.vectors is None:
            return []
        q = _normalize(np.asarray(query_vec, dtype=np.float32))
        scores = self.vectors @ q
        order = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in order]

    @staticmethod
    def rrf(rankings: list[list[tuple[int, float]]], k: int) -> list[tuple[int, float]]:
        """Reciprocal Rank Fusion: robust to BM25/cosine score scales differing."""

        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, (idx, _) in enumerate(ranking):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
        return sorted(fused.items(), key=lambda kv: -kv[1])[:k]

    def hit(self, idx: int, score: float) -> dict[str, Any]:
        c = self.chunks[idx]
        return {"id": c["id"], "doc_id": c["doc_id"], "title": c["title"], "source": c["source"],
                "kind": c["kind"], "text": c["text"], "score": round(score, 4)}


class Retriever:
    """Hybrid retriever; `mode` in {"bm25","dense","hybrid"} (evals compare all three)."""

    def __init__(self, index: KnowledgeIndex, embedder: Embedder | None, top_k: int = 6) -> None:
        self.index = index
        self.embedder = embedder
        self.top_k = top_k

    async def search(self, query: str, mode: str = "hybrid", k: int | None = None) -> list[dict[str, Any]]:
        k = k or self.top_k
        pool = max(k * 5, 30)
        lexical = self.index.bm25_ranking(query, pool)
        if mode == "bm25" or self.embedder is None or self.index.vectors is None:
            return [self.index.hit(i, s) for i, s in lexical[:k]]
        vec = await asyncio.to_thread(self.embedder.embed_query, query_text(query))
        dense = self.index.dense_ranking(vec, pool)
        ranked = dense[:k] if mode == "dense" else self.index.rrf([lexical, dense], k)
        return [self.index.hit(i, s) for i, s in ranked]


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    score: float
    response: str
    matched_query: str = ""


class SemanticCache:
    """Embedding-similarity cache for INFRA_QUESTION answers, persisted as JSONL."""

    def __init__(self, path: Path, embedder: Embedder, threshold: float) -> None:
        self.path = path
        self.embedder = embedder
        self.threshold = threshold
        self.entries: list[dict[str, Any]] = []
        if path.exists():
            self.entries = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]

    async def lookup(self, query: str) -> CacheLookup:
        if not self.entries:
            return CacheLookup(False, 0.0, "")
        vec = np.asarray(await asyncio.to_thread(self.embedder.embed_query, similarity_text(query)))
        matrix = _normalize(np.asarray([e["vector"] for e in self.entries], dtype=np.float32))
        scores = matrix @ _normalize(vec.astype(np.float32))
        best = int(np.argmax(scores))
        score = float(scores[best])
        entry = self.entries[best]
        hit = score >= self.threshold
        return CacheLookup(hit, score, entry["response"] if hit else "", entry["query"])

    async def write(self, query: str, response: str) -> None:
        vec = await asyncio.to_thread(self.embedder.embed_query, similarity_text(query))
        entry = {"query": query, "response": response, "vector": [round(float(x), 6) for x in vec],
                 "created_at": datetime.now(UTC).isoformat()}
        self.entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def format_context(hits: list[dict[str, Any]], max_chars: int = 1800) -> str:
    """Numbered context block; the numbers are what the advisor cites as [n]."""

    parts = []
    for n, h in enumerate(hits, start=1):
        parts.append(f"[{n}] {h['title']} ({h['source']})\n{h['text'][:max_chars]}")
    return "\n\n".join(parts) or "No context retrieved."
