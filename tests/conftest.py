"""Test harness. No live key is ever needed: the LLM boundary runs the
deterministic mock, SuperDocs rendering is disabled, and only the local
Postgres from docker-compose is required (docker compose up -d db).
"""
import os
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Environment must be pinned BEFORE app modules are imported.
os.environ["LLM_PROVIDER"] = "mock"
os.environ["DOCTASK_RENDER"] = "0"
os.environ.setdefault(
    "DATABASE_URL", "postgresql://doctask:doctask@localhost:5433/doctask"
)

import pytest  # noqa: E402

from app import db, service  # noqa: E402

SEED = REPO / "corpus" / "seed"
SEED2 = REPO / "corpus" / "seed2"
EXTRA = REPO / "corpus" / "extra"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    db.init_schema()


@pytest.fixture()
def pile():
    """A fresh, uniquely named pile, deleted (cascade) afterwards."""
    p = service.create_pile(f"test-{uuid.uuid4().hex[:12]}")
    yield p
    db.q("DELETE FROM piles WHERE id=%s", (p["id"],))


def seed_documents(pile_id: str, directory: Path) -> list[dict]:
    docs = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() in {".md", ".txt", ".html", ".docx", ".pdf"}:
            docs.append(service.add_document_bytes(
                pile_id, path.name, path.read_bytes()))
    return docs


def approve_all(run_id: str, decided_by: str = "test") -> int:
    items = service.list_pending(run_id)
    for item in items:
        service.decide_item(item["id"], approve=True, decided_by=decided_by)
    return len(items)


def full_run_approved(pile_id: str) -> dict:
    """Seed-independent helper: run whatever is in the pile to completion,
    approving every gate item."""
    run = service.start_run(pile_id, kind="full", wait=True)
    approve_all(run["id"])
    service.resume_run(run["id"], wait=True)
    return service.get_run(run["id"])
