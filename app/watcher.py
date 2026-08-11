"""The watched location (Movement 3). Drop a file into
WATCH_DIR/<pile-name>/ and it becomes a focused UPDATE run: the new document
is classified and extracted, impact analysis picks the entities it touches,
and only those sections are recomposed — everything else stays byte-identical
and the commit proves it.

Everything present in one sweep is treated as ONE arrival: a bulk drop of
five files becomes a single update run covering all five, not five runs and
five trips through the human gate. The update stays focused either way,
because impact is computed from the entities the batch mentions.

Files are moved to <pile>/processed/ (or /failed/) after handling, so a
restart never re-processes them. If the pile has an active run, the files
simply stay until the next sweep — updates queue instead of colliding.
"""
import logging
import shutil
import threading
import time
from pathlib import Path

from . import config, service

log = logging.getLogger("doctask.watcher")


def sweep_once() -> list[str]:
    root = Path(config.WATCH_DIR)
    root.mkdir(parents=True, exist_ok=True)
    handled = []
    for pile_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if pile_dir.name in ("processed", "failed"):
            continue
        files = sorted(
            f for f in pile_dir.iterdir()
            if f.is_file() and f.suffix.lower() in config.ACCEPTED_FORMATS
        )
        if not files:
            continue
        pile = service.create_pile(pile_dir.name)
        # Ingest the whole batch first. A file this pile cannot read is its
        # own problem: it goes to failed/ and the rest still run.
        batch: list[tuple[Path, str]] = []
        for f in files:
            try:
                doc = service.add_document_path(pile["id"], str(f))
                batch.append((f, doc["id"]))
            except service.ServiceError as exc:
                dest = pile_dir / "failed"
                dest.mkdir(exist_ok=True)
                shutil.move(str(f), dest / f.name)
                log.warning("watcher: failed %s: %s", f.name, exc)
        if not batch:
            continue
        try:
            service.start_run(pile["id"], kind="update",
                              doc_ids=[doc_id for _, doc_id in batch],
                              wait=True)
        except service.ServiceError as exc:
            # Active run (409) or anything else the service refuses: leave
            # every file where it is and retry on a later sweep. The
            # documents are already ingested, so the retry deduplicates by
            # sha256 and reuses them rather than adding them twice.
            log.info("watcher: pile %s not ready (%s), will retry %d file(s)",
                     pile_dir.name, exc, len(batch))
            continue
        dest = pile_dir / "processed"
        dest.mkdir(exist_ok=True)
        for f, _ in batch:
            shutil.move(str(f), dest / f.name)
            handled.append(f.name)
        log.info("watcher: processed %d file(s) into pile %s as one update",
                 len(batch), pile_dir.name)
    return handled


def start_watcher(interval: float = 2.0) -> threading.Event:
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                sweep_once()
            except Exception:
                log.exception("watcher sweep failed")
            stop.wait(interval)

    threading.Thread(target=loop, daemon=True, name="doctask-watcher").start()
    return stop
