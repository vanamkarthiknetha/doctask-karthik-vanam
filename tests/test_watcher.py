"""The watched location: a dropped file becomes an update run that pauses at
the gate; the file is archived to processed/ so restarts never re-ingest it;
files for a busy pile wait for the next sweep."""
import shutil
import uuid

from conftest import EXTRA, SEED, full_run_approved, seed_documents

from app import config, db, service, watcher


def test_sweep_ingests_runs_and_archives(tmp_path, monkeypatch, pile):
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])

    watch = tmp_path / "incoming"
    pile_dir = watch / pile["name"]
    pile_dir.mkdir(parents=True)
    shutil.copy(EXTRA / "brightline-amendment-2.md", pile_dir)
    monkeypatch.setattr(config, "WATCH_DIR", str(watch))

    handled = watcher.sweep_once()
    assert handled == ["brightline-amendment-2.md"]
    assert not (pile_dir / "brightline-amendment-2.md").exists()
    assert (pile_dir / "processed" / "brightline-amendment-2.md").exists()

    runs = service.list_runs(pile["id"])
    update = [r for r in runs if r["kind"] == "update"]
    assert update and update[-1]["status"] == "awaiting_review"

    # a second sweep finds nothing new — restart-safe
    assert watcher.sweep_once() == []


def test_busy_pile_leaves_file_for_next_sweep(tmp_path, monkeypatch):
    p = service.create_pile(f"test-watch-{uuid.uuid4().hex[:8]}")
    try:
        seed_documents(p["id"], SEED)
        service.start_run(p["id"], kind="full", wait=True)  # parks at the gate

        watch = tmp_path / "incoming"
        pile_dir = watch / p["name"]
        pile_dir.mkdir(parents=True)
        shutil.copy(EXTRA / "brightline-amendment-2.md", pile_dir)
        monkeypatch.setattr(config, "WATCH_DIR", str(watch))

        assert watcher.sweep_once() == []
        assert (pile_dir / "brightline-amendment-2.md").exists(), \
            "file must wait, not vanish, while the pile is busy"
    finally:
        db.q("DELETE FROM piles WHERE id=%s", (p["id"],))
