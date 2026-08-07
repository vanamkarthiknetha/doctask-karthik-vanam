"""MCP server: the same operations as the REST API, exposed as tools so an
agent can drive the entire flow — including approval, which is an explicit
operation, not a UI affordance.

Run:  python -m app.mcp_server   (stdio transport)

Client config (Claude Code / Claude Desktop / Cursor):
  {
    "mcpServers": {
      "doctask": {
        "command": "<repo>/.venv/Scripts/python",
        "args": ["-m", "app.mcp_server"],
        "cwd": "<repo>"
      }
    }
  }
"""
import json

from mcp.server.mcpserver import MCPServer

from . import db, service
from .service import ServiceError

mcp = MCPServer("doctask")


def _safe(fn, *args, **kwargs) -> str:
    try:
        return json.dumps(fn(*args, **kwargs), indent=2, default=str)
    except ServiceError as exc:
        return json.dumps({"error": str(exc), "status": exc.status})


@mcp.tool()
def create_pile(name: str) -> str:
    """Create (or fetch) a document pile — an isolated corpus with its own
    register, findings, and runs."""
    return _safe(service.create_pile, name)


@mcp.tool()
def list_piles() -> str:
    """List all piles."""
    return _safe(service.list_piles)


@mcp.tool()
def add_document(pile_id: str, path: str) -> str:
    """Add one document (md/txt/html/docx/pdf) to a pile from a local path."""
    return _safe(service.add_document_path, pile_id, path)


@mcp.tool()
def get_pile_rules(pile_id: str) -> str:
    """The playbook a pile is examined against right now: its own uploaded
    rules if it has any, else the system default (source: 'pile' | 'default')."""
    return _safe(service.get_pile_rules, pile_id)


@mcp.tool()
def set_pile_rules(pile_id: str, rules_yaml: str) -> str:
    """Hand this pile the rules it should be examined against — a staged
    YAML playbook (compliance checklist / contract playbook / style guide).
    Validated against the checks this engine actually implements before
    being stored; rejected with the specific reason if invalid."""
    return _safe(service.set_pile_rules, pile_id, rules_yaml)


@mcp.tool()
def clear_pile_rules(pile_id: str) -> str:
    """Revert a pile to the system default playbook."""
    return _safe(service.clear_pile_rules, pile_id)


@mcp.tool()
def run_analysis(pile_id: str, kind: str = "full",
                 doc_ids: list[str] | None = None) -> str:
    """Run the analysis pipeline synchronously until it completes or pauses
    at the human gate (status awaiting_review). kind: full | update."""
    return _safe(service.start_run, pile_id, kind, doc_ids, True)


@mcp.tool()
def get_run(run_id: str) -> str:
    """Run status, its full stage/decision timeline, and gate item counts."""
    return _safe(service.get_run, run_id)


@mcp.tool()
def list_pending(run_id: str) -> str:
    """List the gate items (section updates, conflicts, findings) still
    awaiting a decision for a run."""
    return _safe(service.list_pending, run_id)


@mcp.tool()
def approve_item(item_id: str, decided_by: str = "mcp-agent",
                 feedback: str | None = None) -> str:
    """Approve ONE pending item. Other items are not affected."""
    return _safe(service.decide_item, item_id, True, decided_by, feedback)


@mcp.tool()
def reject_item(item_id: str, decided_by: str = "mcp-agent",
                feedback: str | None = None) -> str:
    """Reject ONE pending item (with optional feedback). Other items are not
    affected — rejecting one never discards the rest."""
    return _safe(service.decide_item, item_id, False, decided_by, feedback)


@mcp.tool()
def resume_run(run_id: str) -> str:
    """Resume a run past the human gate. Refused (409) while any item is
    still undecided."""
    return _safe(service.resume_run, run_id, True)


@mcp.tool()
def get_register(pile_id: str) -> str:
    """The current vendor obligations register: sections, hashes, claims
    with fact citations, and the rendered markdown."""
    return _safe(service.get_register, pile_id)


@mcp.tool()
def get_audit_log(pile_id: str) -> str:
    """Provenance: what changed, when, because of which source — plus every
    fact with its exact anchor and every conflict with its resolution."""
    return _safe(service.get_audit, pile_id)


@mcp.tool()
def get_findings(pile_id: str) -> str:
    """All findings for a pile with rule, severity, source pointer, status."""
    return _safe(service.list_findings, pile_id)


@mcp.tool()
def get_costs(pile_id: str | None = None, run_id: str | None = None) -> str:
    """Per-run / per-stage spend and latency: LLM tokens, USD, SuperDocs ops."""
    return _safe(service.get_costs, pile_id, run_id)


def main() -> None:
    db.init_schema()
    mcp.run()


if __name__ == "__main__":
    main()
