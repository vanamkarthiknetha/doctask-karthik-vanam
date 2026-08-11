"""Register composition. The deliverable is rendered deterministically from
VERIFIED facts only — the renderer takes fact rows as input, so a claim
without a supporting fact cannot be emitted by construction (and the claims
table's CHECK constraint enforces it a second time at the schema level).

Sections are content-hashed. An update run recomposes only impacted entities;
every other section's hash is untouched, which is the byte-identity proof.
"""
import hashlib
import re
from datetime import date

LABELS = {
    "per_minute_rate": ("Usage rate", lambda v: f"${v}/min"),
    "monthly_commitment_minutes": ("Monthly commitment", lambda v: f"{int(v):,} min"),
    "payment_terms": ("Payment terms", lambda v: v),
    "sla_uptime": ("SLA uptime", lambda v: f"{v}%"),
    "auto_renewal_months": ("Auto-renewal term", lambda v: f"{v} months"),
}
KEY_ORDER = list(LABELS)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def export_stem(pile_name: str) -> str:
    """Filesystem-safe stem for deliverable filenames, derived from the pile's
    human name — exports read register-<pile-name>.docx, not an opaque id."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", pile_name).strip("-.")
    return stem[:60] or "pile"


def _cite(fact: dict, docs: dict[str, dict]) -> str:
    doc = docs[fact["doc_id"]]
    return f"{doc['filename']} (chars {fact['char_start']}-{fact['char_end']})"


def entity_key(entity: str) -> str:
    return "client-" + "".join(c if c.isalnum() else "-" for c in entity.lower())


def compose_entity_section(entity: str, effective: dict, facts: list[dict],
                           docs: dict[str, dict]) -> dict:
    """Build one client's section. Returns {section_key, title, content_md,
    content_hash, claims}."""
    lines = [f"## {entity}", "", "| Obligation | Value | Source |", "|---|---|---|"]
    claims = []
    for key in KEY_ORDER:
        fact = effective.get((entity, key))
        if not fact:
            continue
        label, fmt = LABELS[key]
        value = fmt(fact["value"])
        source = _cite(fact, docs)
        lines.append(f"| {label} | {value} | {source} |")
        claims.append({
            "text": f"{entity}: {label} is {value}",
            "fact_ids": [fact["id"]],
        })

    invoices = {}
    for f in facts:
        doc = docs.get(f["doc_id"])
        if doc and doc["doc_class"] == "invoice" and f["entity"] == entity:
            invoices.setdefault(f["doc_id"], {})[f["key"]] = f
    if invoices:
        def period(doc_id):
            # Order by the document's validated ISO date, never by the
            # model's free-text period ("July 2026" sorts before "June 2026"
            # as a string). The period text is display, not ordering.
            d = docs[doc_id].get("doc_date")
            p = invoices[doc_id].get("invoice_period")
            return (d.isoformat() if d else "0000-00-00",
                    p["value"] if p else "")
        latest_id = max(invoices, key=period)
        by_key = invoices[latest_id]
        parts, fact_ids = [], []
        for key, label in (("invoice_period", "period"), ("billed_minutes", "minutes"),
                           ("billed_amount", "amount"), ("billed_terms", "terms")):
            f = by_key.get(key)
            if f:
                parts.append(f"{label} {f['value']}")
                fact_ids.append(f["id"])
        if parts:
            text = f"Latest invoice: {', '.join(parts)}"
            src = _cite(next(iter(by_key.values())), docs)
            lines += ["", f"{text} — {src}"]
            claims.append({"text": f"{entity}: {text}", "fact_ids": fact_ids})

    content = "\n".join(lines) + "\n"
    return {
        "section_key": entity_key(entity),
        "title": entity,
        "content_md": content,
        "content_hash": _hash(content),
        "claims": claims,
    }


def compose_overview(pile_name: str, entities: list[str], n_docs: int,
                     open_conflicts: int, pending_findings: int) -> dict:
    lines = [
        "# Vendor Obligations Register",
        "",
        f"Pile: {pile_name}",
        "",
        f"Clients: {len(entities)} ({', '.join(sorted(entities)) or 'none'})",
        "",
        f"Source documents: {n_docs}",
        "",
        f"Open conflicts: {open_conflicts}",
        "",
        f"Findings pending review: {pending_findings}",
        "",
        "Every value below cites the exact place in a source document. "
        "A value with no citation cannot appear in this register.",
    ]
    content = "\n".join(lines) + "\n"
    return {
        "section_key": "overview",
        "title": "Overview",
        "content_md": content,
        "content_hash": _hash(content),
        "claims": [],
    }


def register_markdown(sections: list[dict]) -> str:
    ordered = sorted(sections, key=lambda s: (s["section_key"] != "overview",
                                              s["section_key"]))
    stamp = date.today().isoformat()
    return "\n".join(s["content_md"] for s in ordered) + \
        f"\n---\nGenerated by DocTask on {stamp}. All companies are fictional.\n"
