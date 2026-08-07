"""Never bluffs: an extraction whose quote cannot be located verbatim in the
source is retried a bounded number of times, then DROPPED and escalated as a
finding — it never becomes a fact or a register claim."""
import hashlib
import json
import os

from app import service


def test_unverifiable_quote_is_dropped_and_escalated(pile, tmp_path, monkeypatch):
    body = ("SERVICE NOTE Meridian Voice Systems and Test Client agree that "
            "the usage rate is $0.99 per conversation minute.")
    doc_file = tmp_path / "testclient-note.txt"
    doc_file.write_text(body, encoding="utf-8")
    sha = hashlib.sha256(" ".join(body.split()).encode()).hexdigest()

    fixtures = {sha: {
        "filename": "testclient-note.txt",
        "classify": {"doc_class": "contract", "entity": "Test Client",
                     "doc_date": "2026-06-01", "confidence": 0.95,
                     "instruction_like": False},
        "facts": [
            {"key": "per_minute_rate", "value": "0.99",
             "quote": "the usage rate is $0.99 per conversation minute"},
            {"key": "payment_terms", "value": "net-30",
             "quote": "THIS SENTENCE DOES NOT EXIST IN THE DOCUMENT"},
        ],
    }}
    fx_dir = tmp_path / "fixtures"
    fx_dir.mkdir()
    (fx_dir / "extractions.json").write_text(json.dumps(fixtures))
    monkeypatch.setenv("FIXTURES_DIR", str(fx_dir))

    service.add_document_bytes(pile["id"], "testclient-note.txt",
                               doc_file.read_bytes())
    run = service.start_run(pile["id"], kind="full", wait=True)
    for item in service.list_pending(run["id"]):
        service.decide_item(item["id"], approve=True)
    service.resume_run(run["id"], wait=True)
    run = service.get_run(run["id"])
    assert run["status"] == "completed"

    audit = service.get_audit(pile["id"])
    keys = {f["key"] for f in audit["facts"]}
    assert "per_minute_rate" in keys, "the verifiable fact must survive"
    assert "payment_terms" not in keys, "the unverifiable fact must be dropped"

    findings = service.list_findings(pile["id"])
    esc = [f for f in findings if f["rule_id"] == "unverifiable-extraction"]
    assert esc and "DROPPED" in esc[0]["message"]

    retries = [e for e in run["events"]
               if e["stage"] == "ground" and e["decision"] == "retry-extraction"]
    assert len(retries) == 2, "extraction must be retried exactly twice first"
