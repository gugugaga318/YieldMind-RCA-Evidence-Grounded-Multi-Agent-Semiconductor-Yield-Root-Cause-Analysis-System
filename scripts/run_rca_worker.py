"""Run the Batch 23.1 leased PostgreSQL RCA Queue Worker."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from yield_rca_api.audit import PostgresAuditSink  # noqa: E402
from yield_rca_api.memory import MemoryApprovalService, PostgresMemoryStore  # noqa: E402
from yield_rca_api.observability import RCAMetrics, configure_logging  # noqa: E402
from yield_rca_api.store import PostgresRCAJobStore  # noqa: E402
from yield_rca_api.worker import RCAQueueWorker, WorkerSettings  # noqa: E402
from yield_rca_core.hybrid_retrieval import KnowledgeLookupRetriever  # noqa: E402
from yield_rca_core.knowledge_runtime import build_knowledge_retriever  # noqa: E402
from yield_rca_core.knowledge_store import PostgresKnowledgeStore  # noqa: E402
from yield_rca_core.workflow import build_postgres_workflow  # noqa: E402

LOGGER = logging.getLogger("yield_rca_worker")


def build_worker(database_url: str, *, worker_id: str | None = None) -> RCAQueueWorker:
    knowledge_store = PostgresKnowledgeStore(database_url)
    knowledge_retriever: KnowledgeLookupRetriever = build_knowledge_retriever(
        knowledge_store,
        database_url=database_url,
    )
    workflow = build_postgres_workflow(
        database_url,
        knowledge_retriever=knowledge_retriever,
    )
    store = PostgresRCAJobStore(database_url)
    return RCAQueueWorker(
        store=store,
        workflow=workflow,
        audit_sink=PostgresAuditSink(database_url),
        memory_service=MemoryApprovalService(PostgresMemoryStore(database_url)),
        metrics=RCAMetrics(),
        runtime_dataset=os.getenv("YIELD_RCA_DATASET", "golden_case").strip()
        or "golden_case",
        settings=WorkerSettings.from_env(),
        worker_id=worker_id,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("YIELD_RCA_DATABASE_URL", ""),
    )
    parser.add_argument("--worker-id", default=os.getenv("YIELD_RCA_WORKER_ID"))
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim at most one ready Job and exit.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    database_url = args.database_url.strip()
    if not database_url:
        raise SystemExit("YIELD_RCA_DATABASE_URL or --database-url is required")
    configure_logging()
    worker = build_worker(database_url, worker_id=args.worker_id)
    worker.store.check_ready()
    if args.once:
        worker.run_once()
        return
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        LOGGER.info("Worker shutdown requested")
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    worker.run_forever(stop)


if __name__ == "__main__":
    main()
