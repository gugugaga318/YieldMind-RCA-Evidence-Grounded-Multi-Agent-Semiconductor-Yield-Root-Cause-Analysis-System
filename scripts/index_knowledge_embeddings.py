"""Populate pgvector embeddings for approved Knowledge Active-Index Chunks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.hybrid_retrieval import (  # noqa: E402
    SentenceTransformerEmbeddingBackend,
)
from yield_rca_core.knowledge_vector_store import (  # noqa: E402
    PostgresKnowledgeEmbeddingIndexer,
)

DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Index only approved Knowledge Chunks into pgvector."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("TEST_DATABASE_URL")
        or os.getenv("YIELD_RCA_DATABASE_URL")
        or "",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--batch-size", type=int, default=32)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.database_url.strip():
        raise SystemExit("set TEST_DATABASE_URL or pass --database-url")
    backend = SentenceTransformerEmbeddingBackend(
        args.model,
        revision=args.revision,
        device=args.device,
        batch_size=args.batch_size,
    )
    result = PostgresKnowledgeEmbeddingIndexer(
        args.database_url.strip(),
        backend,
    ).sync()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
