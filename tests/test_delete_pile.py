"""Deleting a pile erases everything it owns — database rows across every
table (including graph checkpoints and the cost ledger, which have no FK
cascade), the exported .docx/.pdf register files, and its watched folder —
while leaving every other pile untouched. Deletion is refused only while a
run is actively executing; a run parked at the review gate is abandonable.
"""
import uuid

import pytest
from conftest import SEED, full_run_approved, seed_documents

from app import config, db, service
from app.register import export_stem
from app.service import ServiceError


def test_export_stem_is_filesystem_safe():
    assert export_stem("harbor") == "harbor"
    assert export_stem("demo call #2") == "demo-call-2"
    assert export_stem("///") == "pile"


def test_delete_pile_erases_all_data_and_files(pile, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setattr(config, "WATCH_DIR", str(tmp_path / "incoming"))
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])

    # Stand-ins for rendered exports (render is disabled under tests) plus a
    # legacy id-named export and a watched folder with a processed file.
    exp = tmp_path / "exports"
    exp.mkdir()
    (exp / f"register-{export_stem(pile['name'])}.docx").write_bytes(b"docx")
    (exp / f"register-{pile['id'][:8]}.pdf").write_bytes(b"pdf")
    watch = tmp_path / "incoming" / pile["name"] / "processed"
    watch.mkdir(parents=True)
    (watch / "old.md").write_text("done")

    listed = service.list_exports(pile["id"])
    assert {f["format"] for f in listed} == {"docx", "pdf"}

    threads = [r["thread_id"] for r in
               db.q("SELECT thread_id FROM runs WHERE pile_id=%s", (pile["id"],))]
    out = service.delete_pile(pile["id"])
    assert out["deleted"] == pile["name"]
    assert out["documents"] > 0 and out["runs"] == 1
    assert sorted(out["export_files_removed"]) == sorted([
        f"register-{export_stem(pile['name'])}.docx",
        f"register-{pile['id'][:8]}.pdf",
    ])
    assert out["watch_folder_removed"] is True

    with pytest.raises(ServiceError) as err:
        service.get_pile(pile["id"])
    assert err.value.status == 404
    for table, col in [("documents", "pile_id"), ("facts", "pile_id"),
                       ("sections", "pile_id"), ("findings", "pile_id"),
                       ("conflicts", "pile_id"), ("pending_items", "pile_id"),
                       ("runs", "pile_id"), ("cost_ledger", "pile_id")]:
        rows = db.q(f"SELECT count(*) AS n FROM {table} WHERE {col}=%s",
                    (pile["id"],))
        assert rows[0]["n"] == 0, table
    assert db.one("SELECT count(*) AS n FROM stage_events WHERE run_id IN "
                  "(SELECT id FROM runs WHERE pile_id=%s)",
                  (pile["id"],))["n"] == 0
    assert db.one("SELECT count(*) AS n FROM checkpoints WHERE thread_id = ANY(%s)",
                  (threads,))["n"] == 0
    assert list(exp.iterdir()) == []
    assert not (tmp_path / "incoming" / pile["name"]).exists()


def test_delete_pile_leaves_other_piles_alone(pile):
    other = service.create_pile(f"test-{uuid.uuid4().hex[:12]}")
    try:
        seed_documents(other["id"], SEED)
        service.delete_pile(pile["id"])
        assert len(service.list_documents(other["id"])) > 0
    finally:
        db.q("DELETE FROM piles WHERE id=%s", (other["id"],))


def test_delete_refused_while_run_active_but_allowed_at_gate(pile):
    seed_documents(pile["id"], SEED)
    run = service.start_run(pile["id"], kind="full", wait=True)
    # Parked at the human gate (awaiting_review) — deleting is allowed; it is
    # exactly how a user abandons a run they no longer want to review.
    assert service.get_run(run["id"])["status"] == "awaiting_review"
    db.q("UPDATE runs SET status='running' WHERE id=%s", (run["id"],))
    with pytest.raises(ServiceError) as err:
        service.delete_pile(pile["id"])
    assert err.value.status == 409
    db.q("UPDATE runs SET status='awaiting_review' WHERE id=%s", (run["id"],))
    out = service.delete_pile(pile["id"])
    assert out["deleted"] == pile["name"]
