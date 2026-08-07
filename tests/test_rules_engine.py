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


def test_invoice_restating_contract_term_under_contract_key_conflicts():
    """Pinned to live-model behavior (third drift of this kind): Gemini stated
    the July invoice's terms as payment_terms=net-45 — the CONTRACT key —
    instead of billed_terms. The restatement must hit the same comparison as
    billed_terms, and it must never become the effective value."""
    docs = {"d1": _doc("d1", "contract", "2026-01-01"),
            "d2": _doc("d2", "invoice", "2026-07-31"),
            "d3": _doc("d3", "invoice", "2026-06-30")}
    facts = [
        _fact("f1", "d1", "ClientCo", "payment_terms", "net-30"),
        _fact("f2", "d2", "ClientCo", "payment_terms", "net-45"),  # disagrees
        _fact("f3", "d3", "ClientCo", "payment_terms", "net-30"),  # agrees
    ]
    r = reconcile(facts, docs)
    assert r["effective"][("ClientCo", "payment_terms")]["id"] == "f1", \
        "an invoice must never supersede an authoritative document"
    assert len(r["conflicts"]) == 1
    assert set(r["conflicts"][0]["fact_ids"]) == {"f2", "f1"}


def test_both_key_spellings_raise_one_conflict_not_two():
    docs = {"d1": _doc("d1", "contract", "2026-01-01"),
            "d2": _doc("d2", "invoice", "2026-07-31",
                       filename="inv-july.pdf")}
    facts = [
        _fact("f1", "d1", "ClientCo", "payment_terms", "net-30"),
        _fact("f2", "d2", "ClientCo", "payment_terms", "net-45"),
        _fact("f3", "d2", "ClientCo", "billed_terms", "net-45"),
    ]
    r = reconcile(facts, docs)
    assert len(r["conflicts"]) == 1, \
        "the same disagreement in two spellings is one conflict, not two"


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


def test_latest_invoice_ordered_by_doc_date_not_period_text():
    """Pinned to live-model behavior: Gemini returned invoice_period as
    "June 2026"/"July 2026" — free text where "July" < "June" as a string —
    and the register's "Latest invoice" line silently showed June. Ordering
    must come from the validated document date, never model prose."""
    from app.register import compose_entity_section

    docs = {"inv6": _doc("inv6", "invoice", "2026-06-30"),
            "inv7": _doc("inv7", "invoice", "2026-07-31")}
    facts = [
        _fact("f1", "inv6", "ClientCo", "invoice_period", "June 2026"),
        _fact("f2", "inv6", "ClientCo", "billed_amount", "15300.00"),
        _fact("f3", "inv7", "ClientCo", "invoice_period", "July 2026"),
        _fact("f4", "inv7", "ClientCo", "billed_amount", "11760.00"),
    ]
    section = compose_entity_section("ClientCo", {}, facts, docs)
    assert "July 2026" in section["content_md"]
    assert "15300.00" not in section["content_md"], \
        "the June invoice must not be presented as the latest"


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
