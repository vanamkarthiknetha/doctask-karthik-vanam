"""Deterministic reconciliation. No model calls here: supersession and
conflict detection are rules a human can audit, not vibes.

- Contract-term keys are owned by authoritative documents (contract,
  amendment). The latest-dated authoritative statement is EFFECTIVE; earlier
  authoritative statements are cleanly superseded (recorded, not a conflict).
- Two authoritative documents with the same date disagreeing on a key IS a
  conflict — never silently resolved.
- An invoice restating a contract term differently than the effective value
  is a conflict between sources, surfaced for a human to acknowledge.
"""
CONTRACT_KEYS = {
    "per_minute_rate",
    "monthly_commitment_minutes",
    "payment_terms",
    "sla_uptime",
    "auto_renewal_months",
}
# invoice key -> the contract term it restates
INVOICE_COUNTERPART = {
    "billed_rate": "per_minute_rate",
    "billed_terms": "payment_terms",
}
AUTHORITATIVE = {"contract", "amendment"}


def reconcile(facts: list[dict], docs: dict[str, dict]) -> dict:
    """facts: verified fact rows; docs: {doc_id: document row}.
    Returns {"effective": {(entity, key): fact},
             "superseded": [(old_fact_id, new_fact_id)],
             "conflicts": [{entity, key, fact_ids, detail}]}
    """
    effective: dict[tuple[str, str], dict] = {}
    superseded: list[tuple[str, str]] = []
    conflicts: list[dict] = []

    def doc_date(fact):
        d = docs[fact["doc_id"]].get("doc_date")
        return d.isoformat() if d else "0000-00-00"

    by_ek: dict[tuple[str, str], list[dict]] = {}
    for f in facts:
        doc = docs.get(f["doc_id"])
        if doc is None:
            continue
        if f["key"] in CONTRACT_KEYS and doc["doc_class"] in AUTHORITATIVE:
            by_ek.setdefault((f["entity"], f["key"]), []).append(f)

    for (entity, key), group in by_ek.items():
        group.sort(key=doc_date)
        latest_date = doc_date(group[-1])
        latest = [f for f in group if doc_date(f) == latest_date]
        values = {f["value"] for f in latest}
        if len(values) > 1:
            names = ", ".join(sorted(docs[f["doc_id"]]["filename"] for f in latest))
            conflicts.append({
                "entity": entity, "key": key,
                "fact_ids": [f["id"] for f in latest],
                "detail": f"authoritative documents of the same date disagree "
                          f"on {key}: {sorted(values)} ({names})",
            })
            continue  # no effective value until a human resolves it
        winner = latest[-1]
        effective[(entity, key)] = winner
        for f in group:
            if f["id"] != winner["id"]:
                superseded.append((f["id"], winner["id"]))

    for f in facts:
        counterpart = INVOICE_COUNTERPART.get(f["key"])
        if not counterpart:
            continue
        eff = effective.get((f["entity"], counterpart))
        if eff and eff["value"] != f["value"]:
            doc = docs[f["doc_id"]]
            eff_doc = docs[eff["doc_id"]]
            conflicts.append({
                "entity": f["entity"], "key": counterpart,
                "fact_ids": [f["id"], eff["id"]],
                "detail": f"'{doc['filename']}' states {counterpart}={f['value']} "
                          f"but the effective value from '{eff_doc['filename']}' "
                          f"is {eff['value']}",
            })

    return {"effective": effective, "superseded": superseded, "conflicts": conflicts}
