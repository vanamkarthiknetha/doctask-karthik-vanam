"""The checkpointed analysis graph.

  classify -> extract -> ground -+-> reconcile -> compose -> examine
                  ^______________|                              |
                   (bounded retry)                           propose
                                                                | interrupt()  <- human gate
                                                             commit -> render -> END

Checkpoints land in Postgres after every node, so a killed process resumes
from the last completed node with no finished work redone. The interrupt in
propose IS the human gate: the run cannot reach commit until it is resumed,
and the service layer only resumes once every pending item is decided.
"""
import json
from typing import TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config, db, register, stages, superdocs_client
from .stages import event


class RunState(TypedDict, total=False):
    run_id: str
    pile_id: str
    kind: str                    # full | update
    doc_ids: list[str]
    candidates: dict             # doc_id -> extracted (pre-verification) facts
    retry_docs: list[str]
    retry_counts: dict
    impacted: list[str]
    proposals: list[dict]
    pre_hashes: dict             # section_key -> hash before this run


def propose_node(state: dict) -> dict:
    run_id, pile_id = state["run_id"], state["pile_id"]
    existing = db.one(
        "SELECT count(*) AS n FROM pending_items WHERE run_id=%s", (run_id,)
    )["n"]
    if existing == 0:  # idempotent: interrupt replays this node from the top
        for p in state.get("proposals") or []:
            db.q(
                "INSERT INTO pending_items (run_id, pile_id, item_type, payload) "
                "VALUES (%s,%s,'section_update',%s)",
                (run_id, pile_id, json.dumps({
                    "section_key": p["section_key"], "title": p["title"],
                    "content_md": p["content_md"], "content_hash": p["content_hash"],
                    "old_hash": p.get("old_hash"), "reason": p.get("reason"),
                    "claims": p["claims"],
                })),
            )
        for c in db.q("SELECT * FROM conflicts WHERE run_id=%s AND status='open'",
                      (run_id,)):
            db.q(
                "INSERT INTO pending_items (run_id, pile_id, item_type, ref_id, payload) "
                "VALUES (%s,%s,'conflict',%s,%s)",
                (run_id, pile_id, c["id"], json.dumps({
                    "entity": c["entity"], "key": c["key"], "detail": c["detail"],
                })),
            )
        for f in db.q("SELECT * FROM findings WHERE run_id=%s AND status='pending'",
                      (run_id,)):
            db.q(
                "INSERT INTO pending_items (run_id, pile_id, item_type, ref_id, payload) "
                "VALUES (%s,%s,'finding',%s,%s)",
                (run_id, pile_id, f["id"], json.dumps({
                    "rule_id": f["rule_id"], "severity": f["severity"],
                    "entity": f["entity"], "message": f["message"],
                    "quote": f["quote"],
                })),
            )
    counts = db.one(
        "SELECT count(*) AS n FROM pending_items WHERE run_id=%s AND status='pending'",
        (run_id,),
    )["n"]
    if counts == 0:
        # Either the run truly produced nothing reviewable, or this is the
        # replay after a resume and every item is already decided.
        run = db.one("SELECT status FROM runs WHERE id=%s", (run_id,))
        if run and run["status"] == "awaiting_review":
            event(run_id, "propose", "resumed-all-items-decided", {})
        else:
            event(run_id, "propose", "nothing-to-review", {})
        return {}
    db.q("UPDATE runs SET status='awaiting_review' WHERE id=%s", (run_id,))
    event(run_id, "propose", "awaiting-human", {"pending_items": counts})
    decision = interrupt({"reason": "human review required",
                          "pending_items": counts})
    event(run_id, "propose", "resumed", {"resume_signal": str(decision)})
    return {}


def commit_node(state: dict) -> dict:
    run_id, pile_id = state["run_id"], state["pile_id"]
    db.q("UPDATE runs SET status='committing' WHERE id=%s", (run_id,))
    still_pending = db.one(
        "SELECT count(*) AS n FROM pending_items WHERE run_id=%s AND status='pending'",
        (run_id,),
    )["n"]
    if still_pending:
        raise RuntimeError(
            f"commit refused: {still_pending} items are still undecided"
        )
    items = db.q("SELECT * FROM pending_items WHERE run_id=%s ORDER BY created_at",
                 (run_id,))
    applied = rejected = 0
    for item in items:
        p = item["payload"]
        if item["item_type"] == "section_update":
            if item["status"] == "approved":
                row = db.one(
                    "INSERT INTO sections (pile_id, section_key, title, content_md, "
                    "content_hash, updated_by_run, updated_reason, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,now()) "
                    "ON CONFLICT (pile_id, section_key) DO UPDATE SET "
                    "title=EXCLUDED.title, content_md=EXCLUDED.content_md, "
                    "content_hash=EXCLUDED.content_hash, "
                    "updated_by_run=EXCLUDED.updated_by_run, "
                    "updated_reason=EXCLUDED.updated_reason, updated_at=now() "
                    "RETURNING id",
                    (pile_id, p["section_key"], p["title"], p["content_md"],
                     p["content_hash"], run_id, p.get("reason")),
                )
                db.q("DELETE FROM claims WHERE section_id=%s", (row["id"],))
                for claim in p["claims"]:
                    db.q(
                        "INSERT INTO claims (section_id, text, fact_ids) "
                        "VALUES (%s,%s,%s::uuid[])",
                        (row["id"], claim["text"], claim["fact_ids"]),
                    )
                applied += 1
            else:
                rejected += 1
                event(run_id, "commit", "section-rejected",
                      {"section_key": p["section_key"],
                       "feedback": item["feedback"]})
        elif item["item_type"] == "conflict" and item["ref_id"]:
            status = "acknowledged" if item["status"] == "approved" else "rejected"
            db.q("UPDATE conflicts SET status=%s, decided_by=%s, feedback=%s, "
                 "decided_at=now() WHERE id=%s",
                 (status, item["decided_by"], item["feedback"], item["ref_id"]))
        elif item["item_type"] == "finding" and item["ref_id"]:
            status = "approved" if item["status"] == "approved" else "rejected"
            db.q("UPDATE findings SET status=%s, decided_by=%s, feedback=%s, "
                 "decided_at=now() WHERE id=%s",
                 (status, item["decided_by"], item["feedback"], item["ref_id"]))

    # Byte-identity proof: compare every section hash with its pre-run value.
    proof = {}
    pre = state.get("pre_hashes") or {}
    current = {s["section_key"]: s["content_hash"] for s in
               db.q("SELECT section_key, content_hash FROM sections "
                    "WHERE pile_id=%s", (pile_id,))}
    for key in sorted(set(pre) | set(current)):
        before, after = pre.get(key), current.get(key)
        proof[key] = {"before": before, "after": after,
                      "byte_identical": before == after}
    event(run_id, "commit", "applied",
          {"sections_applied": applied, "sections_rejected": rejected,
           "byte_identity": proof})
    return {}


def render_node(state: dict) -> dict:
    """Render the committed register to styled docx/pdf via SuperDocs.
    Upload + export are free operations (ops bill on edit-apply only).
    A missing key or a SuperDocs failure never fails the run — it is a
    logged decision, and the markdown register remains the deliverable."""
    import os

    run_id, pile_id = state["run_id"], state["pile_id"]
    if os.environ.get("DOCTASK_RENDER", "1") == "0":
        event(run_id, "render", "skipped-render-disabled", {})
        return {}
    if not superdocs_client.has_key():
        event(run_id, "render", "skipped-no-superdocs-key", {})
        return {}
    try:
        import time as _t
        from pathlib import Path

        import markdown as md

        sections = db.q("SELECT * FROM sections WHERE pile_id=%s", (pile_id,))
        if not sections:
            event(run_id, "render", "skipped-empty-register", {})
            return {}
        text = register.register_markdown(sections)
        body = md.markdown(text, extensions=["tables"])
        # The exporter honors INLINE styles only (a <style> block is ignored,
        # verified visually), and needs the background-color LONGHAND — the
        # background: shorthand is dropped while color: is kept, which
        # renders white-on-white (SuperDocs B4).
        body = (
            body
            .replace("<h1>", '<h1 style="color:#123c63;">')
            .replace("<h2>", '<h2 style="color:#1a4f8b;">')
            .replace("<table>", '<table style="border-collapse:collapse;'
                     'width:100%;">')
            .replace("<th>", '<th style="background-color:#1a4f8b;'
                     'color:#ffffff;padding:6px 10px;text-align:left;">')
            .replace("<td>", '<td style="border:1px solid #b9c6d4;'
                     'padding:6px 10px;">')
        )
        html = "<html><body>" + body + "</body></html>"
        session = f"doctask-{pile_id[:8]}-{_t.strftime('%Y%m%d-%H%M%S')}"
        t0 = _t.monotonic()
        superdocs_client.upload_html(session, "register.html", html)
        out_dir = Path(config.EXPORT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for fmt in ("docx", "pdf"):
            blob = superdocs_client.export(session, fmt)
            path = out_dir / f"register-{pile_id[:8]}.{fmt}"
            path.write_bytes(blob)
            written.append(str(path))
        db.q(
            "INSERT INTO cost_ledger (run_id, pile_id, stage, provider, "
            "superdocs_ops, latency_ms) VALUES (%s,%s,'render','superdocs',0,%s)",
            (run_id, pile_id, int((_t.monotonic() - t0) * 1000)),
        )
        event(run_id, "render", "exported",
              {"session": session, "files": written, "superdocs_ops": 0})
    except Exception as exc:  # render is best-effort by design
        event(run_id, "render", "failed-nonfatal", {"error": str(exc)[:300]})
    return {}


def finish_node(state: dict) -> dict:
    db.q("UPDATE runs SET status='completed', finished_at=now() WHERE id=%s",
         (state["run_id"],))
    event(state["run_id"], "finish", "completed", {})
    return {}


_checkpointer: PostgresSaver | None = None
_graph = None


def get_graph():
    global _checkpointer, _graph
    if _graph is None:
        import atexit

        pool = ConnectionPool(
            config.DATABASE_URL, min_size=1, max_size=6, open=True,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        atexit.register(pool.close)
        _checkpointer = PostgresSaver(pool)
        _checkpointer.setup()

        b = StateGraph(RunState)
        b.add_node("classify", stages.classify_node)
        b.add_node("extract", stages.extract_node)
        b.add_node("ground", stages.ground_node)
        b.add_node("reconcile", stages.reconcile_node)
        b.add_node("compose", stages.compose_node)
        b.add_node("examine", stages.examine_node)
        b.add_node("propose", propose_node)
        b.add_node("commit", commit_node)
        b.add_node("render", render_node)
        b.add_node("finish", finish_node)
        b.add_edge(START, "classify")
        b.add_edge("classify", "extract")
        b.add_edge("extract", "ground")
        b.add_conditional_edges("ground", stages.route_after_ground,
                                {"retry": "extract", "continue": "reconcile"})
        b.add_edge("reconcile", "compose")
        b.add_edge("compose", "examine")
        b.add_edge("examine", "propose")
        b.add_edge("propose", "commit")
        b.add_edge("commit", "render")
        b.add_edge("render", "finish")
        b.add_edge("finish", END)
        _graph = b.compile(checkpointer=_checkpointer)
    return _graph


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def run_graph(initial: RunState, thread_id: str) -> dict:
    return get_graph().invoke(initial, config=_cfg(thread_id))


def resume_graph(thread_id: str) -> dict:
    """Resume a graph paused at the human gate."""
    return get_graph().invoke(Command(resume="reviewed"), config=_cfg(thread_id))


def continue_graph(thread_id: str) -> dict:
    """Continue a graph after a process kill (replays from last checkpoint)."""
    return get_graph().invoke(None, config=_cfg(thread_id))
