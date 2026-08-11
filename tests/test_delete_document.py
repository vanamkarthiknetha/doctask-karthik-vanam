"""Removing a source document takes its derived evidence with it, and is
honest about the consequence: the register is never silently rewritten, it
reports itself STALE until an approved run recomposes it.

The staleness signal is the grounding invariant checked directly — a claim
citing a fact that no longer exists — not a flag someone has to remember to
set. It clears itself when the register is recomposed.
"""
import pytest
from conftest import SEED, full_run_approved, seed_documents

from app import db, service
from app.register import LABELS
from app.service import ServiceError


def test_delete_document_removes_its_derived_evidence(pile):
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])
    docs = service.list_documents(pile["id"])
    target = next(d for d in docs if d["status"] == "extracted")

    n_facts = db.one("SELECT count(*) AS n FROM facts WHERE doc_id=%s",
                     (target["id"],))["n"]
    assert n_facts > 0, "pick a document that actually contributed facts"

    out = service.delete_document(target["id"])
    assert out["deleted"] == target["filename"]
    assert out["facts_removed"] == n_facts

    assert db.one("SELECT count(*) AS n FROM documents WHERE id=%s",
                  (target["id"],))["n"] == 0
    assert db.one("SELECT count(*) AS n FROM facts WHERE doc_id=%s",
                  (target["id"],))["n"] == 0
    assert db.one("SELECT count(*) AS n FROM findings WHERE doc_id=%s",
                  (target["id"],))["n"] == 0
    # No conflict may survive citing evidence that is gone.
    orphaned = db.one(
        "SELECT count(*) AS n FROM conflicts c WHERE c.pile_id=%s AND EXISTS ("
        "  SELECT 1 FROM unnest(c.fact_ids) fid "
        "  WHERE NOT EXISTS (SELECT 1 FROM facts f WHERE f.id = fid))",
        (pile["id"],))["n"]
    assert orphaned == 0
    assert len(service.list_documents(pile["id"])) == len(docs) - 1


def test_register_reports_itself_stale_then_heals_on_rerun(pile):
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])
    assert service.get_register(pile["id"])["stale"] is False

    target = next(d for d in service.list_documents(pile["id"])
                  if db.one("SELECT count(*) AS n FROM facts WHERE doc_id=%s",
                            (d["id"],))["n"] > 0)
    out = service.delete_document(target["id"])
    assert out["register_stale"] is True

    reg = service.get_register(pile["id"])
    assert reg["stale"] is True and reg["stale_claims"] > 0
    # The report keeps its content — removal never rewrites it behind the user.
    assert len(reg["sections"]) > 0

    full_run_approved(pile["id"])
    healed = service.get_register(pile["id"])
    assert healed["stale"] is False and healed["stale_claims"] == 0


def test_retracting_an_amendment_revives_the_term_it_superseded(pile):
    """The register's rate comes from the amendment while it is present, and
    goes back to the contract's original rate once the amendment is removed —
    supersession is undone, not left dangling."""
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])
    amendment = next(d for d in service.list_documents(pile["id"])
                     if d["doc_class"] == "amendment")
    superseded = db.q(
        "SELECT f.id, f.key, f.value FROM facts f WHERE f.superseded_by IN "
        "(SELECT id FROM facts WHERE doc_id=%s)", (amendment["id"],))
    assert superseded, "seed corpus should have an amendment superseding a term"

    out = service.delete_document(amendment["id"])
    assert out["facts_revived"] == len(superseded)
    for f in superseded:
        row = db.one("SELECT superseded_by FROM facts WHERE id=%s", (f["id"],))
        assert row["superseded_by"] is None

    full_run_approved(pile["id"])
    markdown = service.get_register(pile["id"])["markdown"]
    assert amendment["filename"] not in markdown
    for f in superseded:
        _, fmt = LABELS[f["key"]]          # the register's own display format
        assert fmt(f["value"]) in markdown


def test_delete_refused_while_a_run_is_active(pile):
    seed_documents(pile["id"], SEED)
    run = service.start_run(pile["id"], kind="full", wait=True)
    assert service.get_run(run["id"])["status"] == "awaiting_review"
    doc = service.list_documents(pile["id"])[0]
    with pytest.raises(ServiceError) as err:
        service.delete_document(doc["id"])
    assert err.value.status == 409


def test_delete_unknown_document_is_404(pile):
    with pytest.raises(ServiceError) as err:
        service.delete_document("00000000-0000-0000-0000-000000000000")
    assert err.value.status == 404
