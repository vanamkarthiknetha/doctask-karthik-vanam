"""Service layer: the machine-drivable operations. REST (app/api.py) and MCP
(app/mcp_server.py) are thin wrappers over these functions — approval itself
is an operation here, not a UI feature.
"""
import threading
import uuid
from pathlib import Path

import psycopg

from . import config, db, graph, ingest
from .rules_engine import load_playbook, parse_playbook_yaml


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def create_pile(name: str) -> dict:
    existing = db.one("SELECT * FROM piles WHERE name=%s", (name,))
    if existing:
        return _pile_out(existing)
    row = db.one("INSERT INTO piles (name) VALUES (%s) RETURNING *", (name,))
    return _pile_out(row)


def list_piles() -> list[dict]:
    return [_pile_out(p) for p in db.q("SELECT * FROM piles ORDER BY created_at")]


def _pile_out(p: dict) -> dict:
    return {"id": str(p["id"]), "name": p["name"],
            "created_at": p["created_at"].isoformat()}


def get_pile(pile_id: str) -> dict:
    p = db.one("SELECT * FROM piles WHERE id=%s", (pile_id,))
    if not p:
        raise ServiceError(404, "pile not found")
    return _pile_out(p)


def _rules_summary(playbook: dict, source: str) -> dict:
    stages = playbook.get("stages", [])
    return {
        "source": source,
        "stages": [s["name"] for s in stages],
        "rule_count": sum(len(s.get("rules", [])) for s in stages),
    }


def get_pile_rules(pile_id: str) -> dict:
    """The playbook this pile is actually examined against right now: its
    own uploaded rules if it has any, else the system default."""
    p = db.one("SELECT rules_yaml FROM piles WHERE id=%s", (pile_id,))
    if p is None:
        raise ServiceError(404, "pile not found")
    if p["rules_yaml"]:
        return _rules_summary(parse_playbook_yaml(p["rules_yaml"]), "pile") | {
            "rules_yaml": p["rules_yaml"],
        }
    default = load_playbook(config.RULES_FILE)
    return _rules_summary(default, "default") | {
        "rules_yaml": Path(config.RULES_FILE).read_text(encoding="utf-8"),
    }


def set_pile_rules(pile_id: str, rules_yaml: str) -> dict:
    """The user hands this pile the rules they care about: a compliance
    checklist, a contract playbook, a style guide — as a staged YAML
    playbook. Rejected with the specific reason if it is malformed or names
    a check this engine cannot enforce; never stored half-understood."""
    get_pile(pile_id)
    try:
        playbook = parse_playbook_yaml(rules_yaml)
    except ValueError as exc:
        raise ServiceError(422, str(exc))
    db.q("UPDATE piles SET rules_yaml=%s WHERE id=%s", (rules_yaml, pile_id))
    return _rules_summary(playbook, "pile")


def clear_pile_rules(pile_id: str) -> dict:
    """Revert this pile to the system default playbook."""
    get_pile(pile_id)
    db.q("UPDATE piles SET rules_yaml=NULL WHERE id=%s", (pile_id,))
    return get_pile_rules(pile_id)


def add_document_bytes(pile_id: str, filename: str, data: bytes) -> dict:
    get_pile(pile_id)
    import tempfile
    from pathlib import Path

    suffix = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        try:
            parsed = ingest.read_document(tmp_path)
        except ValueError as exc:
            raise ServiceError(422, str(exc))
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    parsed["filename"] = filename
    return _insert_document(pile_id, parsed)


def add_document_path(pile_id: str, path: str) -> dict:
    get_pile(pile_id)
    try:
        parsed = ingest.read_document(path)
    except ValueError as exc:
        raise ServiceError(422, str(exc))
    except FileNotFoundError:
        raise ServiceError(404, f"file not found: {path}")
    return _insert_document(pile_id, parsed)


def _insert_document(pile_id: str, parsed: dict) -> dict:
    dupe = db.one(
        "SELECT * FROM documents WHERE pile_id=%s AND sha256=%s",
        (pile_id, parsed["sha256"]),
    )
    if dupe:
        return _doc_out(dupe) | {"duplicate": True}
    row = db.one(
        "INSERT INTO documents (pile_id, filename, format, sha256, raw_text) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING *",
        (pile_id, parsed["filename"], parsed["format"], parsed["sha256"],
         parsed["raw_text"]),
    )
    return _doc_out(row)


def _doc_out(d: dict) -> dict:
    return {
        "id": str(d["id"]), "filename": d["filename"], "format": d["format"],
        "sha256": d["sha256"], "doc_class": d["doc_class"],
        "entity": d["entity"], "status": d["status"],
        "injection_flagged": d["injection_flagged"],
    }


def list_sample_sets() -> list[dict]:
    """Bundled corpus sets a user can load with one click (demo convenience;
    the same files ship in the repo under corpus/)."""
    root = Path(config.CORPUS_DIR)
    if not root.is_dir():
        return []
    sets = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if d.name == "incoming":  # the watched location, not a sample set
            continue
        files = sorted(f.name for f in d.iterdir()
                       if f.is_file() and f.suffix.lower() in config.ACCEPTED_FORMATS)
        if files:
            sets.append({"name": d.name, "files": files})
    return sets


def load_sample_set(pile_id: str, set_name: str) -> list[dict]:
    get_pile(pile_id)
    root = Path(config.CORPUS_DIR)
    target = (root / set_name).resolve()
    if target.parent != root.resolve() or not target.is_dir():
        raise ServiceError(404, f"sample set not found: {set_name}")
    out = []
    for f in sorted(target.iterdir()):
        if f.is_file() and f.suffix.lower() in config.ACCEPTED_FORMATS:
            out.append(add_document_path(pile_id, str(f)))
    if not out:
        raise ServiceError(404, f"sample set is empty: {set_name}")
    return out


def list_documents(pile_id: str) -> list[dict]:
    return [_doc_out(d) for d in
            db.q("SELECT * FROM documents WHERE pile_id=%s ORDER BY added_at",
                 (pile_id,))]


ACTIVE_STATUSES = ("running", "awaiting_review", "committing")


def start_run(pile_id: str, kind: str = "full", doc_ids: list[str] | None = None,
              wait: bool = False) -> dict:
    get_pile(pile_id)
    # Fast-path check: gives a friendly error naming the existing run in the
    # common case. Not the actual guarantee — two concurrent calls can both
    # pass this SELECT before either INSERTs (TOCTOU). The real enforcement
    # is uq_runs_one_active_per_pile below, which the database applies
    # atomically regardless of how many callers race here.
    active = db.one(
        "SELECT id FROM runs WHERE pile_id=%s AND status = ANY(%s)",
        (pile_id, list(ACTIVE_STATUSES)),
    )
    if active:
        raise ServiceError(
            409, f"pile already has an active run {active['id']}; "
            "finish or resume it first",
        )
    if doc_ids is None:
        doc_ids = [str(d["id"]) for d in
                   db.q("SELECT id FROM documents WHERE pile_id=%s", (pile_id,))]
    if not doc_ids and kind == "full":
        raise ServiceError(422, "pile has no documents")
    thread_id = f"run-{uuid.uuid4()}"
    try:
        run = db.one(
            "INSERT INTO runs (pile_id, kind, thread_id, trigger_doc) "
            "VALUES (%s,%s,%s,%s) RETURNING *",
            (pile_id, kind, thread_id,
             doc_ids[0] if kind == "update" and doc_ids else None),
        )
    except psycopg.errors.UniqueViolation:
        raise ServiceError(
            409, "pile already has an active run; finish or resume it first")
    initial = {
        "run_id": str(run["id"]), "pile_id": pile_id, "kind": kind,
        "doc_ids": doc_ids,
    }
    if wait:
        _execute(initial, thread_id)
    else:
        threading.Thread(target=_execute, args=(initial, thread_id),
                         daemon=True).start()
    return get_run(str(run["id"]))


def _execute(initial: dict, thread_id: str) -> None:
    try:
        graph.run_graph(initial, thread_id)
    except Exception as exc:
        db.q("UPDATE runs SET status='failed', error=%s, finished_at=now() "
             "WHERE thread_id=%s", (str(exc)[:500], thread_id))


def resume_run(run_id: str, wait: bool = False) -> dict:
    run = db.one("SELECT * FROM runs WHERE id=%s", (run_id,))
    if not run:
        raise ServiceError(404, "run not found")
    if run["status"] != "awaiting_review":
        raise ServiceError(409, f"run is {run['status']}, not awaiting_review")
    pending = db.one(
        "SELECT count(*) AS n FROM pending_items WHERE run_id=%s "
        "AND status='pending'", (run_id,),
    )["n"]
    if pending:
        raise ServiceError(
            409, f"{pending} pending item(s) are undecided; decide each one "
            "(approve or reject) before resuming",
        )

    def _resume():
        try:
            graph.resume_graph(run["thread_id"])
        except Exception as exc:
            db.q("UPDATE runs SET status='failed', error=%s, finished_at=now() "
                 "WHERE id=%s", (str(exc)[:500], run_id))

    if wait:
        _resume()
    else:
        threading.Thread(target=_resume, daemon=True).start()
    return get_run(run_id)


def continue_incomplete_runs() -> list[str]:
    """Crash recovery at startup: any run left 'running' or 'committing'
    continues from its last checkpoint. Runs awaiting review keep waiting —
    the human gate survives restarts too."""
    resumed = []
    for run in db.q("SELECT * FROM runs WHERE status IN ('running','committing')"):
        thread_id = run["thread_id"]
        run_id = str(run["id"])

        def _cont(tid=thread_id, rid=run_id):
            try:
                graph.continue_graph(tid)
            except Exception as exc:
                db.q("UPDATE runs SET status='failed', error=%s, "
                     "finished_at=now() WHERE id=%s", (str(exc)[:500], rid))

        threading.Thread(target=_cont, daemon=True).start()
        resumed.append(run_id)
    return resumed


def get_run(run_id: str) -> dict:
    run = db.one("SELECT * FROM runs WHERE id=%s", (run_id,))
    if not run:
        raise ServiceError(404, "run not found")
    events = db.q("SELECT stage, decision, detail, ts FROM stage_events "
                  "WHERE run_id=%s ORDER BY id", (run_id,))
    counts = db.one(
        "SELECT count(*) FILTER (WHERE status='pending') AS pending, "
        "count(*) FILTER (WHERE status='approved') AS approved, "
        "count(*) FILTER (WHERE status='rejected') AS rejected "
        "FROM pending_items WHERE run_id=%s", (run_id,),
    )
    return {
        "id": str(run["id"]), "pile_id": str(run["pile_id"]),
        "kind": run["kind"], "status": run["status"],
        "error": run["error"],
        "started_at": run["started_at"].isoformat(),
        "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None,
        "items": {k: counts[k] for k in ("pending", "approved", "rejected")},
        "events": [
            {"stage": e["stage"], "decision": e["decision"],
             "detail": e["detail"], "ts": e["ts"].isoformat()} for e in events
        ],
    }


def list_runs(pile_id: str) -> list[dict]:
    return [get_run(str(r["id"])) for r in
            db.q("SELECT id FROM runs WHERE pile_id=%s ORDER BY started_at",
                 (pile_id,))]


def list_pending(run_id: str, status: str | None = "pending") -> list[dict]:
    sql = "SELECT * FROM pending_items WHERE run_id=%s"
    params: list = [run_id]
    if status:
        sql += " AND status=%s"
        params.append(status)
    return [
        {"id": str(i["id"]), "item_type": i["item_type"],
         "status": i["status"], "payload": i["payload"],
         "decided_by": i["decided_by"], "feedback": i["feedback"]}
        for i in db.q(sql + " ORDER BY created_at", tuple(params))
    ]


def decide_item(item_id: str, approve: bool, decided_by: str = "human",
                feedback: str | None = None) -> dict:
    """Decide ONE item. Other items are untouched by construction — this
    update targets a single row and nothing else."""
    row = db.one(
        "UPDATE pending_items SET status=%s, decided_by=%s, feedback=%s, "
        "decided_at=now() WHERE id=%s AND status='pending' RETURNING *",
        ("approved" if approve else "rejected", decided_by, feedback, item_id),
    )
    if not row:
        current = db.one("SELECT status FROM pending_items WHERE id=%s", (item_id,))
        if not current:
            raise ServiceError(404, "item not found")
        raise ServiceError(409, f"item already decided: {current['status']}")
    return {"id": str(row["id"]), "status": row["status"]}


def get_register(pile_id: str) -> dict:
    get_pile(pile_id)
    sections = db.q("SELECT * FROM sections WHERE pile_id=%s ORDER BY section_key",
                    (pile_id,))
    out = []
    for s in sections:
        claims = db.q("SELECT text, fact_ids FROM claims WHERE section_id=%s",
                      (s["id"],))
        out.append({
            "section_key": s["section_key"], "title": s["title"],
            "content_md": s["content_md"], "content_hash": s["content_hash"],
            "updated_by_run": str(s["updated_by_run"]) if s["updated_by_run"] else None,
            "updated_reason": s["updated_reason"],
            "updated_at": s["updated_at"].isoformat(),
            "claims": [
                {"text": c["text"], "fact_ids": [str(f) for f in c["fact_ids"]]}
                for c in claims
            ],
        })
    from .register import register_markdown

    markdown = register_markdown(sections) if sections else ""
    return {"sections": out, "markdown": markdown}


def get_audit(pile_id: str) -> dict:
    """What changed, when, because of which source."""
    get_pile(pile_id)
    sections = db.q(
        "SELECT s.section_key, s.content_hash, s.updated_at, s.updated_reason, "
        "r.id AS run_id, r.kind, r.trigger_doc, d.filename AS trigger_filename "
        "FROM sections s LEFT JOIN runs r ON r.id = s.updated_by_run "
        "LEFT JOIN documents d ON d.id = r.trigger_doc "
        "WHERE s.pile_id=%s ORDER BY s.section_key", (pile_id,),
    )
    facts = db.q(
        "SELECT f.entity, f.key, f.value, f.quote, f.char_start, f.char_end, "
        "f.superseded_by, d.filename FROM facts f "
        "JOIN documents d ON d.id = f.doc_id "
        "WHERE f.pile_id=%s ORDER BY f.entity, f.key", (pile_id,),
    )
    conflicts = db.q(
        "SELECT entity, key, detail, status, decided_by, feedback, created_at "
        "FROM conflicts WHERE pile_id=%s ORDER BY created_at", (pile_id,),
    )
    return {
        "sections": [
            {"section_key": s["section_key"], "content_hash": s["content_hash"],
             "updated_at": s["updated_at"].isoformat(),
             "updated_reason": s["updated_reason"],
             "updated_by_run": str(s["run_id"]) if s["run_id"] else None,
             "run_kind": s["kind"], "trigger_document": s["trigger_filename"]}
            for s in sections
        ],
        "facts": [
            {"entity": f["entity"], "key": f["key"], "value": f["value"],
             "source": f["filename"],
             "anchor": [f["char_start"], f["char_end"]], "quote": f["quote"],
             "superseded": f["superseded_by"] is not None} for f in facts
        ],
        "conflicts": [
            {"entity": c["entity"], "key": c["key"], "detail": c["detail"],
             "status": c["status"], "decided_by": c["decided_by"],
             "feedback": c["feedback"], "created_at": c["created_at"].isoformat()}
            for c in conflicts
        ],
    }


def get_costs(pile_id: str | None = None, run_id: str | None = None) -> dict:
    where, params = "", ()
    if run_id:
        where, params = "WHERE run_id=%s", (run_id,)
    elif pile_id:
        where, params = "WHERE pile_id=%s", (pile_id,)
    rows = db.q(
        f"SELECT run_id, stage, provider, model, "
        f"sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens, "
        f"sum(superdocs_ops) AS superdocs_ops, sum(usd) AS usd, "
        f"sum(latency_ms) AS latency_ms, count(*) AS calls "
        f"FROM cost_ledger {where} "
        f"GROUP BY run_id, stage, provider, model ORDER BY run_id, stage",
        params,
    )
    total = db.one(
        f"SELECT coalesce(sum(usd),0) AS usd, coalesce(sum(input_tokens),0) AS "
        f"input_tokens, coalesce(sum(output_tokens),0) AS output_tokens, "
        f"coalesce(sum(superdocs_ops),0) AS superdocs_ops, "
        f"coalesce(sum(latency_ms),0) AS latency_ms "
        f"FROM cost_ledger {where}", params,
    )
    return {
        "by_stage": [
            {"run_id": str(r["run_id"]) if r["run_id"] else None,
             "stage": r["stage"], "provider": r["provider"], "model": r["model"],
             "calls": r["calls"], "input_tokens": int(r["input_tokens"]),
             "output_tokens": int(r["output_tokens"]),
             "superdocs_ops": int(r["superdocs_ops"]),
             "usd": float(r["usd"]), "latency_ms": int(r["latency_ms"])}
            for r in rows
        ],
        "total": {k: (float(total[k]) if k == "usd" else int(total[k]))
                  for k in total},
    }


def list_findings(pile_id: str) -> list[dict]:
    rows = db.q(
        "SELECT f.*, d.filename FROM findings f "
        "LEFT JOIN documents d ON d.id = f.doc_id "
        "WHERE f.pile_id=%s ORDER BY f.created_at", (pile_id,),
    )
    return [
        {"id": str(f["id"]), "rule_id": f["rule_id"], "severity": f["severity"],
         "entity": f["entity"], "message": f["message"], "status": f["status"],
         "source": f["filename"], "quote": f["quote"],
         "anchor": ([f["char_start"], f["char_end"]]
                    if f["char_start"] is not None else None)}
        for f in rows
    ]
