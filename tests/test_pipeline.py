"""End-to-end behavior on the seed corpus: the planted defects are found,
the register is grounded, and a clean corpus yields an honest empty report.
"""
from conftest import SEED, SEED2, full_run_approved, seed_documents

from app import db, service


def test_seed_corpus_finds_planted_defects(pile):
    seed_documents(pile["id"], SEED)
    run = full_run_approved(pile["id"])
    assert run["status"] == "completed"

    findings = service.list_findings(pile["id"])
    by_rule = {f["rule_id"] for f in findings}
    assert "R1-auto-renewal-cap" in by_rule          # Corvid 24-month renewal
    assert "R4-invoice-arithmetic" in by_rule        # Halcyon total mismatch
    assert "R5-minimum-commitment" in by_rule        # Brightline under-billing
    assert "R6-injection-quarantine" in by_rule      # Halcyon memo

    audit = service.get_audit(pile["id"])
    conflict_keys = {(c["entity"], c["key"]) for c in audit["conflicts"]}
    assert ("Brightline Health", "per_minute_rate") in conflict_keys
    assert ("Brightline Health", "payment_terms") in conflict_keys
    assert ("Halcyon Support Desk", "per_minute_rate") in conflict_keys

    # Amendment supersedes the MSA: the effective rate in the register is
    # the amendment's, and the MSA fact is recorded as superseded.
    reg = service.get_register(pile["id"])
    bl = next(s for s in reg["sections"]
              if s["section_key"] == "client-brightline-health")
    assert "$0.42/min" in bl["content_md"]
    assert "brightline-amendment-1.html" in bl["content_md"]
    superseded = [f for f in audit["facts"]
                  if f["entity"] == "Brightline Health"
                  and f["key"] == "per_minute_rate" and f["superseded"]]
    assert superseded, "MSA rate fact should be marked superseded"


def test_every_claim_cites_verified_facts(pile):
    """Grounding invariant: every claim's fact_ids point at facts whose quote
    is a verbatim substring of the source document."""
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])
    reg = service.get_register(pile["id"])
    checked = 0
    for section in reg["sections"]:
        for claim in section["claims"]:
            assert claim["fact_ids"], f"claim without facts: {claim['text']}"
            for fid in claim["fact_ids"]:
                fact = db.one("SELECT * FROM facts WHERE id=%s", (fid,))
                assert fact is not None
                doc = db.one("SELECT raw_text FROM documents WHERE id=%s",
                             (fact["doc_id"],))
                assert fact["quote"] in doc["raw_text"]
                assert doc["raw_text"][fact["char_start"]:fact["char_end"]] \
                    == fact["quote"]
                checked += 1
    assert checked > 10


def test_clean_corpus_honest_no_findings(pile):
    """seed2 (Juniper) is clean: zero findings, zero conflicts — and the run
    proves the rules RAN by reporting every rule's outcome."""
    seed_documents(pile["id"], SEED2)
    run = full_run_approved(pile["id"])
    assert run["status"] == "completed"
    assert service.list_findings(pile["id"]) == []
    assert service.get_audit(pile["id"])["conflicts"] == []
    examine_events = [e for e in run["events"]
                      if e["stage"].startswith("examine:")]
    assert examine_events, "rule stages must be reported even when clean"
    for e in examine_events:
        assert e["decision"] == "clean"
        assert all(r["outcome"] == "clean" for r in e["detail"]["rules"])
    reg = service.get_register(pile["id"])
    assert any("Juniper Logistics" in s["content_md"] for s in reg["sections"])


def test_unknown_format_rejected(pile):
    import pytest

    from app.service import ServiceError

    with pytest.raises(ServiceError) as exc:
        service.add_document_bytes(pile["id"], "sheet.xlsx", b"PK\x03\x04junk")
    assert exc.value.status == 422


def test_corrupt_docx_rejected_as_422_not_a_crash(pile):
    """An accepted extension with unreadable content is a system-boundary
    error (honest 422), never an unhandled 500 — python-docx raises
    zipfile.BadZipFile on this, which must not escape read_document."""
    import pytest

    from app.service import ServiceError

    with pytest.raises(ServiceError) as exc:
        service.add_document_bytes(pile["id"], "bad.docx", b"not actually a docx file")
    assert exc.value.status == 422


def test_corrupt_pdf_rejected_as_422_not_a_crash(pile):
    """Same boundary, pypdf's failure mode (PdfStreamError / similar)."""
    import pytest

    from app.service import ServiceError

    with pytest.raises(ServiceError) as exc:
        service.add_document_bytes(pile["id"], "bad.pdf", b"%PDF-1.4 not a real pdf body")
    assert exc.value.status == 422
