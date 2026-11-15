"""Offline fakes: tests never touch the network or need API keys."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest

from infra_assistant.config import Settings
from infra_assistant.models import ModelBundle

ROOT = Path(__file__).resolve().parents[1]


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder (similar texts -> similar vectors)."""

    dim = 64

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim)
        for tok in text.lower().split():
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        return (v / (np.linalg.norm(v) or 1.0)).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class FakeChat:
    """Mimics ChatGoogleGenerativeAI: `responder(messages, schema)` returns the output."""

    def __init__(self, responder: Callable[[list[Any], type | None], Any]) -> None:
        self.responder = responder
        self.calls: list[list[Any]] = []

    def with_structured_output(self, schema: type) -> "FakeChat":
        parent = self

        class _Structured:
            async def ainvoke(self, messages: list[Any]) -> Any:
                parent.calls.append(messages)
                return parent.responder(messages, schema)

        return _Structured()  # type: ignore[return-value]

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)

        class _Msg:
            content = self.responder(messages, None)

        return _Msg()


def fake_bundle(**responders: Callable[[list[Any], type | None], Any]) -> ModelBundle:
    default = lambda m, s: "ok"  # noqa: E731
    return ModelBundle(
        router=FakeChat(responders.get("router", default)),
        collector=FakeChat(responders.get("collector", default)),
        generator=FakeChat(responders.get("generator", default)),
        writer=FakeChat(responders.get("writer", default)),
        judge=FakeChat(responders.get("judge", default)),
        embeddings=FakeEmbedder(),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    chunks = [
        {"id": "c0", "doc_id": "tf:aws_db_instance", "title": "Terraform aws_db_instance - Example Usage",
         "source": "s0", "kind": "terraform", "text": "resource aws_db_instance postgres multi_az storage_encrypted"},
        {"id": "c1", "doc_id": "tf:aws_s3_bucket", "title": "Terraform aws_s3_bucket", "source": "s1",
         "kind": "terraform", "text": "resource aws_s3_bucket bucket versioning public access block"},
        {"id": "c2", "doc_id": "wa:rel_multiaz", "title": "REL10-BP01 Deploy to multiple locations", "source": "s2",
         "kind": "well_architected", "text": "deploy the workload to multiple availability zones"},
    ]
    (index_dir / "chunks.jsonl").write_text("\n".join(json.dumps(c) for c in chunks))
    emb = FakeEmbedder()
    np.save(index_dir / "vectors.npy", np.asarray(emb.embed_documents([c["text"] for c in chunks]), dtype=np.float32))
    return Settings.from_mapping({
        "GOOGLE_API_KEY": "test-key", "INDEX_DIR": str(index_dir),
        "SANDBOX_DIR": str(ROOT / "terraform_sandbox"), "MAX_FOLLOWUP_TURNS": "2",
    })


VALID_HCL = '''
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

resource "aws_eip" "entry" {
  domain = "vpc"
}
'''
