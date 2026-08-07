"""Per-pile rules: the user hands EACH pile the rules it cares about instead
of the whole deployment sharing one file the engineer wrote. Proof, not
assertion: the same corpus, examined under two different active playbooks,
produces two different sets of findings — confirming the pile's uploaded
rules are what a real run enforces, not just what get_pile_rules echoes back.
"""
import uuid

import pytest
from conftest import SEED, full_run_approved, seed_documents

from app import db, service
from app.rules_engine import validate_playbook
from app.service import ServiceError

RELAXED_YAML = """
stages:
  - name: terms-checks
    description: relaxed for this pile only
    rules:
      - id: R1-auto-renewal-cap
        severity: high
        description: Auto-renewal terms must not exceed 60 months
        check: fact_max
        key: auto_renewal_months
        max: 60
"""

BAD_CHECK_YAML = """
stages:
  - name: s
    rules:
      - id: X1
        severity: high
        description: bogus rule
        check: not_a_real_check
"""

# A literal tab character: YAML forbids tabs for indentation, so this is
# guaranteed-invalid rather than a string that merely looks wrong.
MALFORMED_YAML = "stages:\n\t- name: bad\n"


def test_default_pile_has_no_custom_rules(pile):
    rules = service.get_pile_rules(pile["id"])
    assert rules["source"] == "default"
    assert rules["rule_count"] == 7


def test_set_and_clear_pile_rules(pile):
    result = service.set_pile_rules(pile["id"], RELAXED_YAML)
    assert result == {"source": "pile", "stages": ["terms-checks"], "rule_count": 1}
    assert service.get_pile_rules(pile["id"])["source"] == "pile"

    reverted = service.clear_pile_rules(pile["id"])
    assert reverted["source"] == "default"
    assert reverted["rule_count"] == 7


def test_malformed_yaml_rejected_as_422_not_stored(pile):
    with pytest.raises(ServiceError) as exc:
        service.set_pile_rules(pile["id"], MALFORMED_YAML)
    assert exc.value.status == 422
    assert service.get_pile_rules(pile["id"])["source"] == "default", \
        "a rejected upload must never partially take effect"


def test_unknown_check_rejected_by_name_not_silently_skipped(pile):
    with pytest.raises(ServiceError) as exc:
        service.set_pile_rules(pile["id"], BAD_CHECK_YAML)
    assert exc.value.status == 422
    assert "not_a_real_check" in str(exc.value)


def test_validate_playbook_flags_duplicate_and_missing_fields():
    errors = validate_playbook({
        "stages": [{"name": "s", "rules": [
            {"id": "A", "severity": "high", "description": "d", "check": "fact_max", "key": "k", "max": 1},
            {"id": "A", "severity": "high", "description": "d2", "check": "fact_min", "key": "k", "min": 1},
            {"severity": "high", "check": "fact_min"},
        ]}],
    })
    assert any("duplicate rule id" in e for e in errors)
    assert any("missing 'id'" in e for e in errors)
    assert any("missing 'description'" in e for e in errors)


def test_pile_rules_change_what_a_real_run_flags():
    """Corvid's planted 24-month auto-renewal (corpus/seed): the default
    playbook's 12-month cap catches it; a pile that uploaded its own,
    relaxed playbook does not — same documents, different active rules."""
    strict = service.create_pile(f"test-rules-strict-{uuid.uuid4().hex[:8]}")
    relaxed = service.create_pile(f"test-rules-relaxed-{uuid.uuid4().hex[:8]}")
    try:
        service.set_pile_rules(relaxed["id"], RELAXED_YAML)
        seed_documents(strict["id"], SEED)
        seed_documents(relaxed["id"], SEED)

        strict_run = full_run_approved(strict["id"])
        relaxed_run = full_run_approved(relaxed["id"])

        strict_ids = {f["rule_id"] for f in service.list_findings(strict["id"])}
        relaxed_ids = {f["rule_id"] for f in service.list_findings(relaxed["id"])}
        assert "R1-auto-renewal-cap" in strict_ids
        assert "R1-auto-renewal-cap" not in relaxed_ids

        def examine_source(run):
            return next(
                e["detail"]["rules_source"] for e in run["events"]
                if e["stage"] == "examine" and e["decision"] == "completed")

        # The audit trail names which ruleset a run actually used — visible,
        # not just inferable from the findings it produced.
        assert examine_source(strict_run) == "default"
        assert examine_source(relaxed_run) == "pile"
    finally:
        db.q("DELETE FROM piles WHERE id=%s", (strict["id"],))
        db.q("DELETE FROM piles WHERE id=%s", (relaxed["id"],))
