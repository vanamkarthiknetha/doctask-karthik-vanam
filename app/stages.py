"""Graph node implementations. Design rules that every node obeys:

1. State carries only small, JSON-serializable working data; the database is
   the source of truth. That is what makes kill/resume and concurrency work.
2. Every node is IDEMPOTENT: it skips work that is already recorded, because
   after a crash LangGraph replays the interrupted node from its start.
3. Every decision that changes the path (retry, quarantine, escalate, skip)
   is written to stage_events — the run is auditable after the fact.
4. Model output is untrusted until verified. Facts only reach the database
   after their quote is located verbatim in the source text.
"""
import json
import re
from datetime import date

from . import config, db, register
from .llm.boundary import Classification, get_provider
from .reconcile import normalize_key, reconcile
from .rules_engine import resolve_playbook, run_rules

# Deterministic injection heuristics, applied in ADDITION to the model's
# instruction_like signal: either one flags the document.
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(previous|prior|earlier)\s+instructions",
    r"disregard\s+(all\s+|any\s+)?(previous|prior|earlier)",
    r"note\s+to\s+(automated|ai)\s+(assistants|systems|agents)",
    r"you\s+(must|should)\s+(mark|approve|delete|ignore)",
    r"system\s+prompt",
]


def heuristic_injection(text: str) -> str | None:
    for pat in INJECTION_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


# Live models spell the same client differently across documents ("Halcyon",
# "Halcyon Support Desk Ltd", ...). Entity joins (reconcile, rules, sections)
# key on the entity string, so drift silently fragments a client. Deterministic
# canonicalization: drop legal-suffix tokens, then unify with an already-known
# entity in the pile when one name token-prefixes the other (first seen wins).
LEGAL_SUFFIXES = {"llc", "gmbh", "ltd", "inc", "co", "corp", "corporation",
                  "company", "limited", "plc", "sa", "bv"}


def _entity_tokens(name: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return tokens


def canonicalize_entity(raw_name: str, known: list[str]) -> str:
    """Return the canonical display name for raw_name given the pile's
    already-known entities. Assumption (logged): within one pile, a client
    name that token-prefixes another refers to the same client."""
    tokens = _entity_tokens(raw_name)
    if not tokens:
        return raw_name
    for existing in known:
        et = _entity_tokens(existing)
        shorter, longer = sorted((tokens, et), key=len)
        if longer[: len(shorter)] == shorter:
            return existing  # consistent display: first seen wins
    # strip trailing legal-suffix words from the display form, keep casing
    words = raw_name.split()
    while words and re.sub(r"[^a-z0-9]", "", words[-1].lower()) in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words) or raw_name


def event(run_id: str, stage: str, decision: str, detail: dict | None = None) -> None:
    db.q(
        "INSERT INTO stage_events (run_id, stage, decision, detail) "
        "VALUES (%s, %s, %s, %s)",
        (run_id, stage, decision, json.dumps(detail or {})),
    )


def record_cost(run_id: str, pile_id: str, stage: str, res) -> None:
    db.q(
        "INSERT INTO cost_ledger (run_id, pile_id, stage, provider, model, "
        "input_tokens, output_tokens, usd, latency_ms) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (run_id, pile_id, stage, res.provider, res.model,
         res.input_tokens, res.output_tokens, res.usd, res.latency_ms),
    )


def _docs(pile_id: str, doc_ids: list[str]) -> list[dict]:
    if not doc_ids:
        return []
    return db.q(
        "SELECT * FROM documents WHERE pile_id = %s AND id = ANY(%s::uuid[])",
        (pile_id, doc_ids),
    )


def _all_docs(pile_id: str) -> dict[str, dict]:
    return {str(d["id"]): d for d in
            db.q("SELECT * FROM documents WHERE pile_id = %s", (pile_id,))}


def _all_facts(pile_id: str) -> list[dict]:
    rows = db.q(
        "SELECT * FROM facts WHERE pile_id = %s AND superseded_by IS NULL",
        (pile_id,),
    )
    for r in rows:
        r["id"] = str(r["id"])
        r["doc_id"] = str(r["doc_id"])
    return rows


def _insert_finding(pile_id, run_id, rule_id, severity, entity, message,
                    doc_id=None, quote=None, char_start=None, char_end=None):
    """Insert a finding unless an equivalent undecided one already exists —
    update runs re-detect standing violations and must not duplicate them."""
    dupe = db.one(
        "SELECT id FROM findings WHERE pile_id=%s AND rule_id=%s "
        "AND message=%s AND status <> 'rejected'",
        (pile_id, rule_id, message),
    )
    if dupe:
        return None
    row = db.one(
        "INSERT INTO findings (pile_id, run_id, rule_id, severity, entity, "
        "message, doc_id, quote, char_start, char_end) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (pile_id, run_id, rule_id, severity, entity, message, doc_id,
         quote, char_start, char_end),
    )
    return str(row["id"])


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------

def classify_node(state: dict) -> dict:
    run_id, pile_id = state["run_id"], state["pile_id"]
    provider = get_provider()
    for doc in _docs(pile_id, state["doc_ids"]):
        if doc["status"] != "ingested":
            continue  # already done before a crash — never redo finished work
        res = provider.classify(doc["raw_text"])
        record_cost(run_id, pile_id, "classify", res)
        c: Classification = res.data
        heur = heuristic_injection(doc["raw_text"])
        flagged = bool(c.instruction_like or heur)
        quarantine = c.doc_class == "unknown" or c.confidence < 0.6
        known = [r["entity"] for r in db.q(
            "SELECT DISTINCT entity FROM documents WHERE pile_id=%s "
            "AND entity IS NOT NULL", (pile_id,))]
        entity = canonicalize_entity(c.entity, known)
        if entity != c.entity:
            event(run_id, "classify", "entity-canonicalized",
                  {"doc": doc["filename"], "model_said": c.entity,
                   "canonical": entity})
        try:
            doc_date = date.fromisoformat(c.doc_date) if c.doc_date else None
        except ValueError:
            doc_date = None
        db.q(
            "UPDATE documents SET doc_class=%s, entity=%s, doc_date=%s, "
            "class_confidence=%s, injection_flagged=%s, status=%s WHERE id=%s",
            (c.doc_class, entity, doc_date, c.confidence, flagged,
             "quarantined" if quarantine else "classified", doc["id"]),
        )
        if quarantine:
            _insert_finding(
                pile_id, run_id, "classification-escalation", "high", entity,
                f"document '{doc['filename']}' could not be confidently "
                f"classified (class={c.doc_class}, confidence={c.confidence}); "
                "it was quarantined and contributes no facts until a human "
                "reviews it", str(doc["id"]),
            )
            event(run_id, "classify", "quarantined-escalated",
                  {"doc": doc["filename"], "confidence": c.confidence})
        else:
            event(run_id, "classify", "classified",
                  {"doc": doc["filename"], "class": c.doc_class,
                   "entity": c.entity, "injection_flagged": flagged,
                   "heuristic_hit": heur})
    return {}


def extract_node(state: dict) -> dict:
    run_id, pile_id = state["run_id"], state["pile_id"]
    provider = get_provider()
    retry = state.get("retry_docs") or []
    candidates = dict(state.get("candidates") or {})
    for doc in _docs(pile_id, state["doc_ids"]):
        doc_id = str(doc["id"])
        is_retry = doc_id in retry
        if doc["status"] != "classified" and not is_retry:
            continue
        if not is_retry and doc_id in candidates:
            continue  # extracted this run already, awaiting grounding
        c = Classification(
            doc_class=doc["doc_class"], entity=doc["entity"] or "unknown",
            doc_date=None, confidence=doc["class_confidence"] or 0.0,
            instruction_like=doc["injection_flagged"],
        )
        res = provider.extract(doc["raw_text"], c)
        record_cost(run_id, pile_id, "extract", res)
        candidates[doc_id] = [f.model_dump() for f in res.data.facts]
        event(run_id, "extract", "re-extracted" if is_retry else "extracted",
              {"doc": doc["filename"], "candidate_facts": len(candidates[doc_id])})
    return {"candidates": candidates, "retry_docs": []}


def ground_node(state: dict) -> dict:
    """Verify every candidate quote against the source text. Unverifiable
    quotes trigger a bounded re-extract; after that, the fact is DROPPED and
    escalated as a finding — it never reaches the register (never bluff)."""
    run_id, pile_id = state["run_id"], state["pile_id"]
    retry_counts = dict(state.get("retry_counts") or {})
    retry_docs: list[str] = []
    docs = {str(d["id"]): d for d in _docs(pile_id, state["doc_ids"])}
    for doc_id, cand in (state.get("candidates") or {}).items():
        doc = docs.get(doc_id)
        if doc is None or doc["status"] == "extracted":
            continue
        text = doc["raw_text"]
        verified, failed, unknown_keys = [], [], []
        for f in cand:
            key = normalize_key(f["key"])
            if key is None:
                unknown_keys.append(f["key"])
                continue
            f = {**f, "key": key}
            quote = " ".join(str(f["quote"]).split())
            idx = text.find(quote)
            (verified if idx >= 0 else failed).append((f, idx, quote))
        if unknown_keys:
            event(run_id, "ground", "unknown-keys-dropped",
                  {"doc": doc["filename"], "keys": unknown_keys})
        if failed and retry_counts.get(doc_id, 0) < 2:
            retry_counts[doc_id] = retry_counts.get(doc_id, 0) + 1
            retry_docs.append(doc_id)
            event(run_id, "ground", "retry-extraction",
                  {"doc": doc["filename"], "attempt": retry_counts[doc_id],
                   "unverified": [f["key"] for f, _, _ in failed]})
            continue
        with db.pool().connection() as conn:
            for f, idx, quote in verified:
                conn.execute(
                    "INSERT INTO facts (pile_id, doc_id, run_id, entity, key, "
                    "value, quote, char_start, char_end) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (pile_id, doc_id, run_id, doc["entity"] or "unknown",
                     f["key"], f["value"], quote, idx, idx + len(quote)),
                )
            conn.execute("UPDATE documents SET status='extracted' WHERE id=%s",
                         (doc_id,))
        for f, _, quote in failed:
            _insert_finding(
                pile_id, run_id, "unverifiable-extraction", "high",
                doc["entity"],
                f"extraction claimed {f['key']}={f['value']} in "
                f"'{doc['filename']}' but its quote could not be located in "
                "the source text after retries; the fact was DROPPED, not "
                "guessed", doc_id,
            )
        event(run_id, "ground", "verified",
              {"doc": doc["filename"], "verified": len(verified),
               "dropped": len(failed)})
    return {"retry_docs": retry_docs, "retry_counts": retry_counts}


def route_after_ground(state: dict) -> str:
    return "retry" if state.get("retry_docs") else "continue"


def reconcile_node(state: dict) -> dict:
    run_id, pile_id = state["run_id"], state["pile_id"]
    docs = _all_docs(pile_id)
    facts = _all_facts(pile_id)
    r = reconcile(facts, docs)
    for old_id, new_id in r["superseded"]:
        db.q("UPDATE facts SET superseded_by=%s WHERE id=%s "
             "AND superseded_by IS NULL", (new_id, old_id))
    new_conflicts = 0
    for c in r["conflicts"]:
        ids = sorted(c["fact_ids"])
        dupe = db.one(
            "SELECT id FROM conflicts WHERE pile_id=%s AND entity=%s AND "
            "key=%s AND fact_ids @> %s::uuid[] AND fact_ids <@ %s::uuid[]",
            (pile_id, c["entity"], c["key"], ids, ids),
        )
        if dupe:
            continue
        db.q(
            "INSERT INTO conflicts (pile_id, run_id, entity, key, fact_ids, "
            "detail) VALUES (%s,%s,%s,%s,%s::uuid[],%s)",
            (pile_id, run_id, c["entity"], c["key"], ids, c["detail"]),
        )
        new_conflicts += 1
    if state["kind"] == "update":
        impacted = sorted({d["entity"] for d in _docs(pile_id, state["doc_ids"])
                           if d["entity"]})
    else:
        impacted = sorted({d["entity"] for d in docs.values() if d["entity"]})
    event(run_id, "reconcile", "completed",
          {"superseded": len(r["superseded"]), "new_conflicts": new_conflicts,
           "impacted_entities": impacted})
    return {"impacted": impacted}


def compose_node(state: dict) -> dict:
    """Recompose ONLY impacted entity sections (plus the overview). The
    pre-run hash of every section is captured here so commit can prove
    byte-identity of everything untouched."""
    run_id, pile_id = state["run_id"], state["pile_id"]
    docs = _all_docs(pile_id)
    facts = _all_facts(pile_id)
    r = reconcile(facts, docs)
    current = {s["section_key"]: s for s in
               db.q("SELECT * FROM sections WHERE pile_id=%s", (pile_id,))}
    pre_hashes = {k: s["content_hash"] for k, s in current.items()}

    proposals = []
    for entity in state.get("impacted") or []:
        if entity == "unknown":
            continue
        sec = register.compose_entity_section(entity, r["effective"], facts, docs)
        old = current.get(sec["section_key"])
        if old and old["content_hash"] == sec["content_hash"]:
            continue  # byte-identical — nothing to propose
        sec["old_hash"] = old["content_hash"] if old else None
        sec["reason"] = (
            f"{'update from new document(s)' if state['kind'] == 'update' else 'full analysis'}"
        )
        proposals.append(sec)

    pile = db.one("SELECT name FROM piles WHERE id=%s", (pile_id,))
    entities = sorted({d["entity"] for d in docs.values()
                       if d["entity"] and d["entity"] != "unknown"})
    n_open = db.one("SELECT count(*) AS n FROM conflicts WHERE pile_id=%s "
                    "AND status='open'", (pile_id,))["n"]
    n_pending = db.one("SELECT count(*) AS n FROM findings WHERE pile_id=%s "
                       "AND status='pending'", (pile_id,))["n"]
    ov = register.compose_overview(pile["name"], entities, len(docs), n_open, n_pending)
    old_ov = current.get("overview")
    if not old_ov or old_ov["content_hash"] != ov["content_hash"]:
        ov["old_hash"] = old_ov["content_hash"] if old_ov else None
        ov["reason"] = "overview counts refresh"
        proposals.append(ov)

    event(run_id, "compose", "proposed",
          {"changed_sections": [p["section_key"] for p in proposals],
           "unchanged_sections": [k for k in pre_hashes
                                  if k not in {p["section_key"] for p in proposals}]})
    return {"proposals": proposals, "pre_hashes": pre_hashes}


def examine_node(state: dict) -> dict:
    run_id, pile_id = state["run_id"], state["pile_id"]
    docs = _all_docs(pile_id)
    facts = _all_facts(pile_id)
    r = reconcile(facts, docs)
    facts_by_doc: dict[str, list[dict]] = {}
    for f in facts:
        facts_by_doc.setdefault(f["doc_id"], []).append(f)
    claims = [c for p in (state.get("proposals") or []) for c in p["claims"]]
    pile_row = db.one("SELECT rules_yaml FROM piles WHERE id=%s", (pile_id,))
    playbook, rules_source = resolve_playbook(
        pile_row["rules_yaml"] if pile_row else None, config.RULES_FILE)
    findings, report = run_rules(playbook, {
        "effective": r["effective"],
        "facts_by_doc": facts_by_doc,
        "docs": docs,
        "claims": claims,
    })
    inserted = 0
    for f in findings:
        if _insert_finding(pile_id, run_id, f["rule_id"], f["severity"],
                           f["entity"], f["message"], f["doc_id"], f["quote"],
                           f["char_start"], f["char_end"]):
            inserted += 1
    for stage in {row["stage"] for row in report}:
        rows = [row for row in report if row["stage"] == stage]
        event(run_id, f"examine:{stage}",
              "violations" if any(x["outcome"] == "violations" for x in rows)
              else "clean", {"rules": rows})
    event(run_id, "examine", "completed",
          {"new_findings": inserted, "rules_evaluated": len(report),
           "rules_source": rules_source})
    return {}
