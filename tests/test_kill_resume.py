"""Survives being stopped: hard-kill the worker process mid-run, continue
from the checkpoint in a fresh process, and prove no finished work was
redone by counting real LLM-boundary calls across both processes."""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from conftest import REPO

from app import db, graph, service

CHILD = """
import sys
sys.path.insert(0, {repo!r})
from pathlib import Path
from app import db, service
db.init_schema()
pile = service.create_pile({pile!r})
for p in sorted(Path({seed!r}).iterdir()):
    if p.suffix.lower() in {{'.md', '.txt', '.html', '.docx', '.pdf'}}:
        service.add_document_bytes(pile['id'], p.name, p.read_bytes())
service.start_run(pile['id'], kind='full', wait=True)
"""


def test_kill_midrun_resume_no_rework(tmp_path):
    pile_name = f"test-kill-{uuid.uuid4().hex[:10]}"
    call_log = tmp_path / "calls.log"
    env = os.environ.copy()
    env.update({
        "LLM_PROVIDER": "mock",
        "DOCTASK_RENDER": "0",
        "MOCK_LLM_CALL_LOG": str(call_log),
        "MOCK_LLM_DELAY_MS": "250",
        "DATABASE_URL": os.environ.get(
            "DATABASE_URL", "postgresql://doctask:doctask@localhost:5433/doctask"),
    })
    script = CHILD.format(repo=str(REPO), pile=pile_name,
                          seed=str(REPO / "corpus" / "seed"))
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def calls():
        if not call_log.exists():
            return []
        return [ln.split("\t") for ln in
                call_log.read_text().strip().splitlines() if ln]

    # Wait until classification is finished and extraction has begun, then
    # hard-kill the process (TerminateProcess — no cleanup, no goodbyes).
    deadline = time.time() + 120
    while time.time() < deadline:
        ops = [c[0] for c in calls()]
        if "extract" in ops:
            break
        if proc.poll() is not None:
            raise AssertionError("child finished before it could be killed")
        time.sleep(0.05)
    proc.kill()
    proc.wait(timeout=30)

    classify_calls_before = sum(1 for c in calls() if c[0] == "classify")
    n_docs = 10
    assert classify_calls_before == n_docs, "classification phase had finished"

    pile = db.one("SELECT id FROM piles WHERE name=%s", (pile_name,))
    run = db.one("SELECT * FROM runs WHERE pile_id=%s", (pile["id"],))
    assert run["status"] == "running", "killed run is still marked running"

    # Continue in THIS process from the Postgres checkpoint.
    os.environ["MOCK_LLM_CALL_LOG"] = str(call_log)
    try:
        graph.continue_graph(run["thread_id"])
    finally:
        os.environ.pop("MOCK_LLM_CALL_LOG", None)
        os.environ.pop("MOCK_LLM_DELAY_MS", None)

    run_after = service.get_run(str(run["id"]))
    assert run_after["status"] == "awaiting_review", \
        "resumed run reaches the human gate"

    # THE claim: classification had finished before the kill, and resuming
    # did not redo it — not one additional classify call was made.
    classify_calls_total = sum(1 for c in calls() if c[0] == "classify")
    assert classify_calls_total == n_docs, (
        f"finished work was redone: {classify_calls_total} classify calls "
        f"for {n_docs} documents"
    )
    # And the run still produced its full result set downstream.
    assert run_after["items"]["pending"] > 5

    db.q("DELETE FROM piles WHERE id=%s", (pile["id"],))
