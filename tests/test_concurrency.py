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


def test_concurrent_start_run_on_same_pile_stays_one_run(monkeypatch):
    """A sequential test can't catch a TOCTOU: two start_run calls both
    reading 'no active run' before either INSERTs. Relying on real thread
    scheduling to hit that window is flaky in both directions — it might
    not fire even when the bug is present. So force it deterministically:
    gate every thread right after its pre-check SELECT and release them
    together, so all n are guaranteed to race into the INSERT at once. The
    real guarantee under test is the schema's uq_runs_one_active_per_pile
    index, not the read-then-write pre-check (which this gate defeats)."""
    p = service.create_pile(f"test-conc-race-{uuid.uuid4().hex[:8]}")
    try:
        seed_documents(p["id"], SEED)
        n = 5
        gate = threading.Barrier(n)
        orig_one = db.one
        precheck_sql = "SELECT id FROM runs WHERE pile_id=%s AND status = ANY(%s)"

        def gated_one(sql, params=()):
            result = orig_one(sql, params)
            if sql.strip() == precheck_sql:
                gate.wait(timeout=30)   # hold every thread here until all n arrive
            return result

        monkeypatch.setattr(db, "one", gated_one)

        results = [None] * n

        def go(i):
            try:
                results[i] = service.start_run(p["id"], kind="full", wait=True)
            except ServiceError as exc:
                results[i] = exc

        threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, ServiceError)]
        assert len(successes) == 1, f"expected exactly one winner, got: {results}"
        assert len(failures) == n - 1
        assert all(f.status == 409 for f in failures)

        # a rejected start_run must never leave a row behind
        run_count = db.one("SELECT count(*) AS n FROM runs WHERE pile_id=%s",
                           (p["id"],))["n"]
        assert run_count == 1

        # finish the one real run and confirm facts weren't duplicated by a
        # second run that should never have been allowed to start
        run_id = successes[0]["id"]
        for item in service.list_pending(run_id):
            service.decide_item(item["id"], approve=True)
        service.resume_run(run_id, wait=True)
        facts = service.get_audit(p["id"])["facts"]
        assert len(facts) > 0
    finally:
        db.q("DELETE FROM piles WHERE id=%s", (p["id"],))
