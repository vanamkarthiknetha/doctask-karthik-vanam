"""The watched location (Movement 3). Drop a file into
WATCH_DIR/<pile-name>/ and it becomes a focused UPDATE run: the new document
is classified and extracted, impact analysis picks the entities it touches,
and only those sections are recomposed — everything else stays byte-identical
and the commit proves it.

Files are moved to <pile>/processed/ (or /failed/) after handling, so a
restart never re-processes them. If the pile has an active run, the file
simply stays until the next sweep — updates queue instead of colliding.
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
        for f in files:
            try:
                doc = service.add_document_path(pile["id"], str(f))
                service.start_run(pile["id"], kind="update",
                                  doc_ids=[doc["id"]], wait=True)
                dest = pile_dir / "processed"
                dest.mkdir(exist_ok=True)
                shutil.move(str(f), dest / f.name)
                handled.append(f.name)
                log.info("watcher: processed %s into pile %s", f.name,
                         pile_dir.name)
            except service.ServiceError as exc:
                if exc.status == 409:  # active run — leave the file, retry later
                    log.info("watcher: pile %s busy, will retry %s",
                             pile_dir.name, f.name)
                    break
                dest = pile_dir / "failed"
                dest.mkdir(exist_ok=True)
                shutil.move(str(f), dest / f.name)
                log.warning("watcher: failed %s: %s", f.name, exc)
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
