"""REST surface. Thin wrappers over app/service.py — the same operations the
MCP server exposes, so a program can drive the whole flow end to end,
approval included.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, service, watcher
from .llm.boundary import get_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    service.continue_incomplete_runs()
    stop = watcher.start_watcher()
    yield
    stop.set()


app = FastAPI(title="DocTask — The Analyst That Never Sleeps", lifespan=lifespan)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.ServiceError as exc:
        raise HTTPException(exc.status, str(exc))


class PileIn(BaseModel):
    name: str


class RunIn(BaseModel):
    kind: str = "full"
    doc_ids: list[str] | None = None
    wait: bool = False


class DecisionIn(BaseModel):
    approve: bool
    decided_by: str = "human"
    feedback: str | None = None


@app.get("/health")
def health():
    return {"ok": True, "llm_provider": get_provider().name}


@app.post("/piles")
def create_pile(body: PileIn):
    return _wrap(service.create_pile, body.name)


@app.get("/piles")
def list_piles():
    return service.list_piles()


@app.get("/piles/{pile_id}/documents")
def list_documents(pile_id: str):
    return _wrap(service.list_documents, pile_id)


@app.post("/piles/{pile_id}/documents")
async def upload_document(pile_id: str, file: UploadFile):
    data = await file.read()
    return _wrap(service.add_document_bytes, pile_id, file.filename, data)


@app.post("/piles/{pile_id}/runs")
def start_run(pile_id: str, body: RunIn):
    return _wrap(service.start_run, pile_id, body.kind, body.doc_ids, body.wait)


@app.get("/piles/{pile_id}/runs")
def list_runs(pile_id: str):
    return _wrap(service.list_runs, pile_id)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    return _wrap(service.get_run, run_id)


@app.get("/runs/{run_id}/pending")
def list_pending(run_id: str, status: str | None = None):
    return _wrap(service.list_pending, run_id, status)


@app.post("/items/{item_id}/decision")
def decide(item_id: str, body: DecisionIn):
    return _wrap(service.decide_item, item_id, body.approve, body.decided_by,
                 body.feedback)


@app.post("/runs/{run_id}/resume")
def resume(run_id: str, wait: bool = False):
    return _wrap(service.resume_run, run_id, wait)


@app.get("/piles/{pile_id}/register")
def get_register(pile_id: str):
    return _wrap(service.get_register, pile_id)


@app.get("/piles/{pile_id}/audit")
def get_audit(pile_id: str):
    return _wrap(service.get_audit, pile_id)


@app.get("/piles/{pile_id}/findings")
def list_findings(pile_id: str):
    return _wrap(service.list_findings, pile_id)


@app.get("/piles/{pile_id}/costs")
def pile_costs(pile_id: str):
    return service.get_costs(pile_id=pile_id)


@app.get("/runs/{run_id}/costs")
def run_costs(run_id: str):
    return service.get_costs(run_id=run_id)


_ui = Path(config.REPO_ROOT) / "ui" / "dist"
if _ui.exists():
    app.mount("/ui", StaticFiles(directory=str(_ui), html=True), name="ui")

    @app.get("/")
    def root():
        return FileResponse(str(_ui / "index.html"))
