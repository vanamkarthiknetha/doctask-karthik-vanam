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


def test_creating_a_pile_creates_its_watched_folder(tmp_path, monkeypatch):
    """The UI tells the user to drop files into corpus/incoming/<pile>/, so
    that folder must exist from the moment the pile does — no convention to
    guess, no folder to create by hand."""
    monkeypatch.setattr(config, "WATCH_DIR", str(tmp_path / "incoming"))
    name = f"test-watch-{uuid.uuid4().hex[:8]}"
    p = service.create_pile(name)
    try:
        assert (tmp_path / "incoming" / name).is_dir()
        # A name that could never be paired with a folder is skipped, not
        # allowed to create a nested directory the watcher would misread.
        assert service.watch_dir_for("a/b") is None
        assert service.watch_dir_for("..") is None
    finally:
        db.q("DELETE FROM piles WHERE id=%s", (p["id"],))


def test_batch_arrival_is_a_single_update_run(tmp_path, monkeypatch):
    """Five files landing together are ONE arrival: one update run covering
    all of them, so a bulk drop costs one trip through the human gate rather
    than one per file."""
    p = service.create_pile(f"test-watch-{uuid.uuid4().hex[:8]}")
    try:
        watch = tmp_path / "incoming"
        pile_dir = watch / p["name"]
        pile_dir.mkdir(parents=True)
        names = ["brightline-msa.md", "corvid-msa.md", "halcyon-msa.md"]
        for n in names:
            shutil.copy(SEED / n, pile_dir)
        monkeypatch.setattr(config, "WATCH_DIR", str(watch))

        handled = watcher.sweep_once()
        assert sorted(handled) == sorted(names)

        runs = service.list_runs(p["id"])
        assert len(runs) == 1, "one arrival, one run — not one run per file"
        assert runs[0]["kind"] == "update"
        assert runs[0]["status"] == "awaiting_review"
        assert len(service.list_documents(p["id"])) == len(names)
        for n in names:
            assert (pile_dir / "processed" / n).exists()
    finally:
        db.q("DELETE FROM piles WHERE id=%s", (p["id"],))


def test_unreadable_file_fails_alone_and_the_batch_still_runs(tmp_path,
                                                              monkeypatch):
    """One bad file must not cost the others their run."""
    p = service.create_pile(f"test-watch-{uuid.uuid4().hex[:8]}")
    try:
        watch = tmp_path / "incoming"
        pile_dir = watch / p["name"]
        pile_dir.mkdir(parents=True)
        shutil.copy(SEED / "brightline-msa.md", pile_dir)
        (pile_dir / "broken.docx").write_bytes(b"not really a docx")
        monkeypatch.setattr(config, "WATCH_DIR", str(watch))

        handled = watcher.sweep_once()
        assert handled == ["brightline-msa.md"]
        assert (pile_dir / "failed" / "broken.docx").exists()
        assert (pile_dir / "processed" / "brightline-msa.md").exists()
        runs = service.list_runs(p["id"])
        assert len(runs) == 1 and runs[0]["status"] == "awaiting_review"
    finally:
        db.q("DELETE FROM piles WHERE id=%s", (p["id"],))


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
