"""Stays alive (Movement 3): a new arrival produces a FOCUSED update — the
touched section changes, every other section stays byte-identical, and the
commit records the proof. The audit answers what changed, when, and because
of which source."""
import pytest
from conftest import EXTRA, SEED, full_run_approved, seed_documents

from app import service
from app.service import ServiceError


def test_new_amendment_updates_only_its_entity(pile):
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])
    before = {s["section_key"]: s["content_hash"]
              for s in service.get_register(pile["id"])["sections"]}

    doc = service.add_document_bytes(
        pile["id"], "brightline-amendment-2.md",
        (EXTRA / "brightline-amendment-2.md").read_bytes(),
    )
    run = service.start_run(pile["id"], kind="update", doc_ids=[doc["id"]],
                            wait=True)
    items = service.list_pending(run["id"])
    changed_keys = {i["payload"]["section_key"] for i in items
                    if i["item_type"] == "section_update"}
    # Focused: only Brightline (and the overview counters) are proposed.
    assert "client-brightline-health" in changed_keys
    assert "client-corvid-recruiting" not in changed_keys
    assert "client-halcyon-support-desk" not in changed_keys
    for item in items:
        service.decide_item(item["id"], approve=True)
    service.resume_run(run["id"], wait=True)
    run = service.get_run(run["id"])
    assert run["status"] == "completed"

    after = {s["section_key"]: s["content_hash"]
             for s in service.get_register(pile["id"])["sections"]}
    assert after["client-brightline-health"] != before["client-brightline-health"]
    assert after["client-corvid-recruiting"] == before["client-corvid-recruiting"]
    assert after["client-halcyon-support-desk"] == before["client-halcyon-support-desk"]

    # The system can PROVE it: the commit's byte-identity record.
    commit = next(e for e in run["events"] if e["stage"] == "commit")
    proof = commit["detail"]["byte_identity"]
    assert proof["client-corvid-recruiting"]["byte_identical"] is True
    assert proof["client-halcyon-support-desk"]["byte_identical"] is True
    assert proof["client-brightline-health"]["byte_identical"] is False

    # The register now shows the new effective rate with the new source.
    reg = service.get_register(pile["id"])
    bl = next(s for s in reg["sections"]
              if s["section_key"] == "client-brightline-health")
    assert "$0.40/min" in bl["content_md"]
    assert "brightline-amendment-2.md" in bl["content_md"]

    # Audit trail: which run, which kind, because of which source.
    audit = service.get_audit(pile["id"])
    bl_audit = next(s for s in audit["sections"]
                    if s["section_key"] == "client-brightline-health")
    assert bl_audit["run_kind"] == "update"
    assert bl_audit["trigger_document"] == "brightline-amendment-2.md"


def test_ui_style_update_targets_only_unanalyzed_documents(pile):
    """The UI's "Analyze N new" button sends kind=update with the ids of the
    documents still awaiting analysis. Same focused guarantee as the watched
    folder, driven by a click: untouched clients stay byte-identical and the
    trigger document is recorded."""
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])
    before = {s["section_key"]: s["content_hash"]
              for s in service.get_register(pile["id"])["sections"]}

    service.add_document_bytes(
        pile["id"], "brightline-amendment-2.md",
        (EXTRA / "brightline-amendment-2.md").read_bytes(),
    )
    fresh = [d["id"] for d in service.list_documents(pile["id"])
             if d["status"] == "ingested"]
    assert len(fresh) == 1, "only the newly uploaded document is unanalyzed"

    run = service.start_run(pile["id"], kind="update", doc_ids=fresh, wait=True)
    for item in service.list_pending(run["id"]):
        service.decide_item(item["id"], approve=True)
    service.resume_run(run["id"], wait=True)

    after = {s["section_key"]: s["content_hash"]
             for s in service.get_register(pile["id"])["sections"]}
    assert after["client-brightline-health"] != before["client-brightline-health"]
    assert after["client-corvid-recruiting"] == before["client-corvid-recruiting"]
    audit = service.get_audit(pile["id"])
    bl = next(s for s in audit["sections"]
              if s["section_key"] == "client-brightline-health")
    assert bl["run_kind"] == "update"
    assert bl["trigger_document"] == "brightline-amendment-2.md"


def test_run_rejects_documents_from_another_pile(pile):
    """doc_ids now arrive from a browser, so they are validated against the
    pile instead of being silently ignored by the later uuid[] filter."""
    seed_documents(pile["id"], SEED)
    other = service.create_pile("test-foreign-doc-pile")
    try:
        foreign = service.add_document_bytes(
            other["id"], "brightline-amendment-2.md",
            (EXTRA / "brightline-amendment-2.md").read_bytes())
        with pytest.raises(ServiceError) as err:
            service.start_run(pile["id"], kind="update", doc_ids=[foreign["id"]])
        assert err.value.status == 422
        with pytest.raises(ServiceError) as err:
            service.start_run(pile["id"], kind="update", doc_ids=["not-a-uuid"])
        assert err.value.status == 422
    finally:
        service.delete_pile(other["id"])


def test_update_run_cost_is_update_sized(pile):
    """An update analyzes ONE document, not the pile: its LLM call count is
    a fraction of the full run's."""
    seed_documents(pile["id"], SEED)
    full = full_run_approved(pile["id"])
    doc = service.add_document_bytes(
        pile["id"], "brightline-amendment-2.md",
        (EXTRA / "brightline-amendment-2.md").read_bytes(),
    )
    upd = service.start_run(pile["id"], kind="update", doc_ids=[doc["id"]],
                            wait=True)
    for item in service.list_pending(upd["id"]):
        service.decide_item(item["id"], approve=True)
    service.resume_run(upd["id"], wait=True)

    full_calls = sum(r["calls"] for r in
                     service.get_costs(run_id=full["id"])["by_stage"]
                     if r["stage"] in ("classify", "extract"))
    upd_calls = sum(r["calls"] for r in
                    service.get_costs(run_id=upd["id"])["by_stage"]
                    if r["stage"] in ("classify", "extract"))
    assert full_calls >= 20   # 10 documents x 2 boundary calls
    assert upd_calls == 2     # 1 document  x 2 boundary calls
