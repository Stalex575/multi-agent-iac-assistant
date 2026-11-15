"""CRISP-DM Data Preparation: clean + chunk raw docs, then embed with Gemini.

  python scripts/build_index.py              # chunk + embed (needs GOOGLE_API_KEY)
  python scripts/build_index.py --no-embed   # chunk only (BM25 works without a key)

Chunking strategy
- Terraform docs: split by markdown `##`/`###` headings, drop front-matter and the
  long "Attribute Reference"/"Import"/"Timeouts" sections (they describe *outputs*,
  not how to write config). Keep "Example Usage" and "Argument Reference".
- Well-Architected pages: one doc per best practice; split into ~1200-char windows
  with overlap, prefixed by the BP id/title so each chunk is self-describing.
Embedding is resumable: vectors are saved in batches to vectors.partial.npy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infra_assistant.store import document_text  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "index"
DROP_SECTIONS = re.compile(r"^(attribute reference|attributes reference|import|timeouts)", re.I)
MAX_CHARS, OVERLAP = 1200, 200


def windows(text: str, size: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        cut = max(text.rfind("\n", start + size // 2, end), text.rfind(". ", start + size // 2, end) + 1)
        end = cut if cut > start and end < len(text) else end
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return out


def terraform_chunks() -> list[dict]:
    chunks = []
    for path in sorted((RAW / "terraform_aws").glob("*.md")):
        resource = path.stem
        body = re.sub(r"^---.*?---", "", path.read_text(encoding="utf-8"), count=1, flags=re.S)
        sections = re.split(r"\n(?=#{2,3} )", body)
        for sec in sections:
            heading = sec.splitlines()[0].lstrip("# ").strip() if sec.strip() else ""
            if DROP_SECTIONS.match(heading) or len(sec.strip()) < 80:
                continue
            for i, w in enumerate(windows(sec)):
                chunks.append({
                    "doc_id": f"tf:{resource}",
                    "title": f"Terraform {resource} - {heading or 'overview'}",
                    "source": f"https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/{resource.removeprefix('aws_')}",
                    "kind": "terraform",
                    "text": w,
                })
    return chunks


def well_architected_chunks() -> list[dict]:
    chunks = []
    for path in sorted((RAW / "well_architected").glob("*.json")):
        page = json.loads(path.read_text(encoding="utf-8"))
        text = page["text"]
        # Drop the long resources/related-docs tail; it is mostly link titles.
        text = re.split(r"\nResources\n|\nRelated documents:", text)[0]
        # HTML -> text left a newline at every inline tag; re-flow into prose.
        text = re.sub(r"\s+", " ", text).strip()
        for w in windows(text):
            chunks.append({
                "doc_id": f"wa:{path.stem}",
                "title": f"{page['title']} [{page['pillar']}]",
                "source": page["url"],
                "kind": "well_architected",
                "text": w,
            })
    return chunks


def representatives(chunks: list[dict]) -> tuple[list[int], list[str]]:
    """One text per document: its title + the start of its first two chunks.

    The Gemini free tier counts every embedded text as a request (100/min, 1000/day), so embedding all
    2315 chunks is not possible. Dense search therefore works at document level, BM25 at chunk level.
    """

    rows: list[int] = []
    texts: dict[str, str] = {}
    for i, c in enumerate(chunks):
        if c["doc_id"] not in texts:
            rows.append(i)
            texts[c["doc_id"]] = c["text"]
        elif len(texts[c["doc_id"]]) < 1500:
            texts[c["doc_id"]] += "\n" + c["text"]
    docs = [document_text(chunks[i]["title"], texts[chunks[i]["doc_id"]][:1500]) for i in rows]
    return rows, docs


def embed(texts: list[str], batch: int = 15, pause_s: float = 15.0) -> np.ndarray:
    """15 texts (~6k tokens) every 15 s keeps us under the 100 RPM / 30k TPM free-tier limits."""

    from infra_assistant.config import Settings
    from infra_assistant.models import build_embedder

    embedder = build_embedder(Settings.load())
    partial = OUT / "vectors.partial.npy"
    vectors = list(np.load(partial)) if partial.exists() else []
    for start in range(len(vectors), len(texts), batch):
        for attempt in range(5):
            try:
                vectors.extend(embedder.embed_documents(texts[start:start + batch]))
                break
            except Exception as exc:  # rate limits / network drops
                wait = 30 * (attempt + 1)
                print(f"embed batch {start} failed ({exc.__class__.__name__}: {str(exc)[:120]}); retry in {wait}s", flush=True)
                time.sleep(wait)
        else:
            raise SystemExit("embedding failed repeatedly; re-run to resume")
        np.save(partial, np.asarray(vectors, dtype=np.float32))
        print(f"embedded {len(vectors)}/{len(texts)}", flush=True)
        time.sleep(pause_s)
    return np.asarray(vectors, dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-embed", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    chunks = terraform_chunks() + well_architected_chunks()
    for i, c in enumerate(chunks):
        c["id"] = f"c{i:05d}"
    (OUT / "chunks.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n", encoding="utf-8")
    kinds = {k: sum(c["kind"] == k for c in chunks) for k in ("terraform", "well_architected")}
    print(f"chunks: {len(chunks)} {kinds}, docs: {len({c['doc_id'] for c in chunks})}")
    if not args.no_embed:
        rows, texts = representatives(chunks)
        vectors = embed(texts)
        np.save(OUT / "vectors.npy", vectors)
        (OUT / "vector_rows.json").write_text(json.dumps(rows))
        (OUT / "vectors.partial.npy").unlink(missing_ok=True)
        print(f"saved vectors {vectors.shape}")


if __name__ == "__main__":
    main()
