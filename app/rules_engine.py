"""Staged rules engine. The playbook (rules/playbook.yaml) is data; this
module implements the generic checks it references. Every finding carries an
exact source pointer (doc, quote, char offsets) taken from the verified fact
it is based on — a finding can never point at text that does not exist.

run_rules() returns (findings, report) where report lists EVERY rule that was
evaluated with its outcome, so "no findings" is a demonstrated result, not an
absence of work.
"""
import re
from decimal import Decimal, InvalidOperation

import yaml


def _num(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").replace("%", ""))
    except InvalidOperation:
        return None


def _net_days(value: str) -> int | None:
    m = re.search(r"net[\s-]?(\d+)", str(value), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _pointer(fact: dict) -> dict:
    return {
        "doc_id": fact["doc_id"],
        "quote": fact["quote"],
        "char_start": fact["char_start"],
        "char_end": fact["char_end"],
    }


def _finding(rule: dict, entity: str | None, message: str, pointer: dict | None) -> dict:
    return {
        "rule_id": rule["id"],
        "severity": rule["severity"],
        "entity": entity,
        "message": message,
        **(pointer or {"doc_id": None, "quote": None, "char_start": None, "char_end": None}),
    }


# Each check receives ctx:
#   effective: {(entity, key): fact}          — post-reconciliation contract terms
#   facts_by_doc: {doc_id: [fact]}            — all verified facts
#   docs: {doc_id: document row}
#   claims: [{"text":..., "fact_ids":[...]}]  — proposed register claims
def check_fact_max(rule, ctx):
    out = []
    for (entity, key), fact in ctx["effective"].items():
        if key != rule["key"]:
            continue
        v = _num(fact["value"])
        if v is not None and v > Decimal(str(rule["max"])):
            out.append(_finding(
                rule, entity,
                f"{key} is {fact['value']}, above the allowed maximum {rule['max']}",
                _pointer(fact),
            ))
    return out


def check_fact_min(rule, ctx):
    out = []
    for (entity, key), fact in ctx["effective"].items():
        if key != rule["key"]:
            continue
        v = _num(fact["value"])
        if v is not None and v < Decimal(str(rule["min"])):
            out.append(_finding(
                rule, entity,
                f"{key} is {fact['value']}, below the required minimum {rule['min']}",
                _pointer(fact),
            ))
    return out


def check_net_terms_max(rule, ctx):
    out = []
    for (entity, key), fact in ctx["effective"].items():
        if key != rule["key"]:
            continue
        days = _net_days(fact["value"])
        if days is not None and days > rule["max_days"]:
            out.append(_finding(
                rule, entity,
                f"payment terms {fact['value']} exceed net-{rule['max_days']}",
                _pointer(fact),
            ))
    return out


def check_injection_flag(rule, ctx):
    out = []
    for doc in ctx["docs"].values():
        if doc["injection_flagged"]:
            out.append(_finding(
                rule, doc.get("entity"),
                f"document '{doc['filename']}' contains text that attempts to "
                "instruct the analysis system; it was treated as data and its "
                "instructions were NOT followed",
                {"doc_id": doc["id"], "quote": None, "char_start": None, "char_end": None},
            ))
    return out


def check_invoice_arithmetic(rule, ctx):
    out = []
    tolerance = Decimal(str(rule.get("tolerance", 0.01)))
    for doc_id, facts in ctx["facts_by_doc"].items():
        by_key = {f["key"]: f for f in facts}
        minutes = by_key.get("billed_minutes")
        rate = by_key.get("billed_rate")
        amount = by_key.get("billed_amount")
        if not (minutes and rate and amount):
            continue
        m, r, a = _num(minutes["value"]), _num(rate["value"]), _num(amount["value"])
        if None in (m, r, a):
            continue
        expected = m * r
        if abs(expected - a) > tolerance:
            doc = ctx["docs"][doc_id]
            out.append(_finding(
                rule, doc.get("entity"),
                f"invoice '{doc['filename']}' states {minutes['value']} minutes at "
                f"{rate['value']}/min (= {expected}) but a total of {amount['value']}",
                _pointer(amount),
            ))
    return out


def check_minimum_commitment(rule, ctx):
    out = []
    for doc_id, facts in ctx["facts_by_doc"].items():
        by_key = {f["key"]: f for f in facts}
        minutes = by_key.get("billed_minutes")
        if not minutes:
            continue
        doc = ctx["docs"][doc_id]
        entity = doc.get("entity")
        commitment = ctx["effective"].get((entity, "monthly_commitment_minutes"))
        if not commitment:
            continue
        m, c = _num(minutes["value"]), _num(commitment["value"])
        if m is not None and c is not None and m < c:
            out.append(_finding(
                rule, entity,
                f"invoice '{doc['filename']}' bills {minutes['value']} minutes, "
                f"below the effective monthly commitment of {commitment['value']}",
                _pointer(minutes),
            ))
    return out


def check_claims_cited(rule, ctx):
    out = []
    for claim in ctx["claims"]:
        if not claim.get("fact_ids"):
            out.append(_finding(
                rule, None,
                f"register claim has no supporting fact: {claim['text'][:120]!r}",
                None,
            ))
    return out


CHECKS = {
    "fact_max": check_fact_max,
    "fact_min": check_fact_min,
    "net_terms_max": check_net_terms_max,
    "injection_flag": check_injection_flag,
    "invoice_arithmetic": check_invoice_arithmetic,
    "minimum_commitment": check_minimum_commitment,
    "claims_cited": check_claims_cited,
}


def load_playbook(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_playbook(playbook: dict) -> list[str]:
    """Structural validation only: every rule must name one of the checks
    this engine actually implements. A rule the engine cannot enforce is
    rejected up front, not silently skipped at run time — the playbook is
    user-supplied input, and an unenforceable rule is a bluff waiting to
    happen (a compliance stage that quietly checks nothing)."""
    errors: list[str] = []
    if not isinstance(playbook, dict):
        return ["playbook must be a YAML mapping with a top-level 'stages' key"]
    stages = playbook.get("stages")
    if not isinstance(stages, list) or not stages:
        return ["playbook must have a non-empty top-level 'stages' list"]
    seen_ids: set[str] = set()
    for si, stage in enumerate(stages):
        if not isinstance(stage, dict) or not stage.get("name"):
            errors.append(f"stages[{si}] is missing a 'name'")
            continue
        rules = stage.get("rules")
        if not isinstance(rules, list) or not rules:
            errors.append(f"stage '{stage['name']}' has no rules")
            continue
        for ri, rule in enumerate(rules):
            where = f"stage '{stage['name']}' rules[{ri}]"
            if not isinstance(rule, dict):
                errors.append(f"{where} is not a mapping")
                continue
            rid = rule.get("id")
            if not rid:
                errors.append(f"{where} is missing 'id'")
            elif rid in seen_ids:
                errors.append(f"duplicate rule id '{rid}'")
            else:
                seen_ids.add(rid)
            label = rid or where
            if not rule.get("severity"):
                errors.append(f"rule '{label}' is missing 'severity'")
            if not rule.get("description"):
                errors.append(f"rule '{label}' is missing 'description'")
            check = rule.get("check")
            if not check:
                errors.append(f"rule '{label}' is missing 'check'")
            elif check not in CHECKS:
                errors.append(
                    f"rule '{label}' uses unknown check '{check}'; known "
                    f"checks: {', '.join(sorted(CHECKS))}"
                )
    return errors


def parse_playbook_yaml(text: str) -> dict:
    """Parse + validate a user-supplied playbook. Raises ValueError with a
    human-readable reason on either malformed YAML or a structurally invalid
    playbook — callers turn this straight into an honest 422, not a 500."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    errors = validate_playbook(data)
    if errors:
        raise ValueError("; ".join(errors))
    return data


def resolve_playbook(rules_yaml: str | None, default_path: str) -> tuple[dict, str]:
    """The pile's own uploaded playbook if it has one, else the system
    default. Callers record which source was used — the examine stage's
    decision, not an implementation detail."""
    if rules_yaml:
        return yaml.safe_load(rules_yaml), "pile"
    return load_playbook(default_path), "default"


def run_rules(playbook: dict, ctx: dict) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    report: list[dict] = []
    for stage in playbook["stages"]:
        for rule in stage["rules"]:
            check = CHECKS.get(rule["check"])
            if check is None:
                report.append({"stage": stage["name"], "rule_id": rule["id"],
                               "outcome": "skipped-unknown-check"})
                continue
            hits = check(rule, ctx)
            findings.extend(hits)
            report.append({
                "stage": stage["name"],
                "rule_id": rule["id"],
                "description": rule["description"],
                "outcome": "violations" if hits else "clean",
                "violations": len(hits),
            })
    return findings, report
