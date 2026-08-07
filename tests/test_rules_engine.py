"""Unit-level checks of the rules engine and reconciliation, independent of
the pipeline."""
from app.reconcile import reconcile
from app.rules_engine import load_playbook, run_rules

from app import config


def _fact(fid, doc, entity, key, value):
    return {"id": fid, "doc_id": doc, "entity": entity, "key": key,
            "value": value, "quote": f"{key} is {value}",
            "char_start": 0, "char_end": 10}


def _doc(did, cls, d, entity="ClientCo", filename=None):
    import datetime

    return {"id": did, "doc_class": cls,
            "doc_date": datetime.date.fromisoformat(d), "entity": entity,
            "filename": filename or f"{did}.md", "injection_flagged": False}


def test_amendment_supersedes_and_invoice_conflicts():
    docs = {"d1": _doc("d1", "contract", "2026-01-01"),
            "d2": _doc("d2", "amendment", "2026-03-01"),
            "d3": _doc("d3", "invoice", "2026-04-01")}
    facts = [
        _fact("f1", "d1", "ClientCo", "per_minute_rate", "0.50"),
        _fact("f2", "d2", "ClientCo", "per_minute_rate", "0.45"),
        _fact("f3", "d3", "ClientCo", "billed_rate", "0.50"),
    ]
    r = reconcile(facts, docs)
    assert r["effective"][("ClientCo", "per_minute_rate")]["id"] == "f2"
    assert ("f1", "f2") in r["superseded"]
    assert len(r["conflicts"]) == 1
    assert r["conflicts"][0]["key"] == "per_minute_rate"
    assert set(r["conflicts"][0]["fact_ids"]) == {"f3", "f2"}


def test_same_date_disagreement_is_conflict_with_no_effective_value():
    docs = {"d1": _doc("d1", "contract", "2026-01-01"),
            "d2": _doc("d2", "contract", "2026-01-01")}
    facts = [
        _fact("f1", "d1", "ClientCo", "payment_terms", "net-30"),
        _fact("f2", "d2", "ClientCo", "payment_terms", "net-60"),
    ]
    r = reconcile(facts, docs)
    assert ("ClientCo", "payment_terms") not in r["effective"], \
        "a disputed value must never be silently resolved"
    assert len(r["conflicts"]) == 1


def test_playbook_clean_context_reports_every_rule_clean():
    playbook = load_playbook(config.RULES_FILE)
    findings, report = run_rules(playbook, {
        "effective": {}, "facts_by_doc": {}, "docs": {}, "claims": [],
    })
    assert findings == []
    rule_ids = {r["rule_id"] for r in report}
    assert len(rule_ids) == 7, "every playbook rule reports an outcome"
    assert all(r["outcome"] == "clean" for r in report)


def test_entity_canonicalization_unifies_model_spelling_drift():
    from app.stages import canonicalize_entity

    # First document seen establishes the display name (suffix stripped).
    assert canonicalize_entity("Brightline Health LLC", []) == "Brightline Health"
    # Later spellings unify to the known entity...
    known = ["Halcyon Support Desk"]
    assert canonicalize_entity("Halcyon", known) == "Halcyon Support Desk"
    assert canonicalize_entity("Halcyon Support Desk Ltd", known) == \
        "Halcyon Support Desk"
    # ...but unrelated clients never merge.
    assert canonicalize_entity("Corvid Recruiting GmbH", known) == \
        "Corvid Recruiting"
    assert canonicalize_entity("Juniper Logistics Co", ["Corvid Recruiting"]) \
        == "Juniper Logistics"


def test_key_normalization_rescues_decorated_keys_and_drops_garbage():
    from app.reconcile import normalize_key

    # Live models decorate keys with prompt category labels — rescue them.
    assert normalize_key("contracts/amendments/per_minute_rate") == "per_minute_rate"
    assert normalize_key("invoices/billed_minutes") == "billed_minutes"
    assert normalize_key("Billed Rate") == "billed_rate"
    assert normalize_key("per_minute_rate") == "per_minute_rate"
    # Garbage keys never enter the fact store.
    assert normalize_key("contracts/amendments") is None
    assert normalize_key("random_key") is None


def test_playbook_detects_violations():
    playbook = load_playbook(config.RULES_FILE)
    docs = {"d1": _doc("d1", "contract", "2026-01-01")}
    effective = {
        ("ClientCo", "auto_renewal_months"):
            _fact("f1", "d1", "ClientCo", "auto_renewal_months", "36"),
        ("ClientCo", "payment_terms"):
            _fact("f2", "d1", "ClientCo", "payment_terms", "net-60"),
        ("ClientCo", "sla_uptime"):
            _fact("f3", "d1", "ClientCo", "sla_uptime", "98.0"),
    }
    findings, _ = run_rules(playbook, {
        "effective": effective, "facts_by_doc": {}, "docs": docs,
        "claims": [{"text": "uncited claim", "fact_ids": []}],
    })
    ids = {f["rule_id"] for f in findings}
    assert ids == {"R1-auto-renewal-cap", "R2-payment-terms-cap",
                   "R3-sla-floor", "R7-claims-cited"}
