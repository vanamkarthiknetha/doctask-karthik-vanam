"""Concurrent runs stay isolated: two piles analyzed simultaneously end with
exactly their own documents' facts, and a second run on a busy pile is
refused instead of corrupting state."""
import threading
import uuid

import pytest
from conftest import SEED, SEED2, seed_documents

from app import db, service
from app.service import ServiceError


def test_two_piles_run_concurrently_without_crosstalk():
    p1 = service.create_pile(f"test-conc-a-{uuid.uuid4().hex[:8]}")
    p2 = service.create_pile(f"test-conc-b-{uuid.uuid4().hex[:8]}")
    try:
        seed_documents(p1["id"], SEED)
        seed_documents(p2["id"], SEED2)
        results: dict = {}

        def go(pile, key):
            try:
                results[key] = service.start_run(pile["id"], kind="full",
                                                 wait=True)
            except Exception as exc:  # pragma: no cover
                results[key] = exc

        t1 = threading.Thread(target=go, args=(p1, "a"))
        t2 = threading.Thread(target=go, args=(p2, "b"))
        t1.start(); t2.start()
        t1.join(timeout=300); t2.join(timeout=300)
        assert not isinstance(results.get("a"), Exception), results.get("a")
        assert not isinstance(results.get("b"), Exception), results.get("b")

        for pile, run_key in ((p1, "a"), (p2, "b")):
            run = service.get_run(results[run_key]["id"])
            assert run["status"] == "awaiting_review"

        ents1 = {f["entity"] for f in service.get_audit(p1["id"])["facts"]}
        ents2 = {f["entity"] for f in service.get_audit(p2["id"])["facts"]}
        assert "Juniper Logistics" not in ents1
        assert ents2 == {"Juniper Logistics"}
        # decide + finish both so no state is left half-open
        for pile, run_key in ((p1, "a"), (p2, "b")):
            for item in service.list_pending(results[run_key]["id"]):
                service.decide_item(item["id"], approve=True)
            service.resume_run(results[run_key]["id"], wait=True)
    finally:
        db.q("DELETE FROM piles WHERE id=%s", (p1["id"],))
        db.q("DELETE FROM piles WHERE id=%s", (p2["id"],))


def test_second_run_on_busy_pile_is_refused(pile):
    seed_documents(pile["id"], SEED)
    run = service.start_run(pile["id"], kind="full", wait=True)  # awaiting gate
    with pytest.raises(ServiceError) as exc:
        service.start_run(pile["id"], kind="full")
    assert exc.value.status == 409
    # the original run is untouched
    assert service.get_run(run["id"])["status"] == "awaiting_review"
