"""The human gate: item-by-item decisions, rejection isolation, and the
machine-drivable approval contract."""
import pytest
from conftest import SEED, seed_documents

from app import service
from app.service import ServiceError


def test_reject_one_finding_keeps_the_rest(pile):
    seed_documents(pile["id"], SEED)
    run = service.start_run(pile["id"], kind="full", wait=True)
    assert service.get_run(run["id"])["status"] == "awaiting_review"

    items = service.list_pending(run["id"])
    assert len(items) > 5
    reject_id = next(i["id"] for i in items
                     if i["item_type"] == "finding"
                     and i["payload"]["rule_id"] == "R5-minimum-commitment")
    for item in items:
        service.decide_item(item["id"], approve=item["id"] != reject_id,
                            decided_by="reviewer",
                            feedback="not a violation in our reading"
                            if item["id"] == reject_id else None)
    service.resume_run(run["id"], wait=True)
    assert service.get_run(run["id"])["status"] == "completed"

    findings = {f["rule_id"]: f for f in service.list_findings(pile["id"])}
    assert findings["R5-minimum-commitment"]["status"] == "rejected"
    # Rejecting one never discards the rest:
    assert findings["R1-auto-renewal-cap"]["status"] == "approved"
    assert findings["R6-injection-quarantine"]["status"] == "approved"
    # Approved sections were still committed:
    assert service.get_register(pile["id"])["sections"]


def test_resume_refused_while_items_pending(pile):
    seed_documents(pile["id"], SEED)
    run = service.start_run(pile["id"], kind="full", wait=True)
    with pytest.raises(ServiceError) as exc:
        service.resume_run(run["id"], wait=True)
    assert exc.value.status == 409
    assert "undecided" in str(exc.value)
    # still awaiting after refusal; nothing was committed
    assert service.get_run(run["id"])["status"] == "awaiting_review"
    assert service.get_register(pile["id"])["sections"] == []


def test_item_cannot_be_decided_twice(pile):
    seed_documents(pile["id"], SEED)
    run = service.start_run(pile["id"], kind="full", wait=True)
    item = service.list_pending(run["id"])[0]
    service.decide_item(item["id"], approve=True)
    with pytest.raises(ServiceError) as exc:
        service.decide_item(item["id"], approve=False)
    assert exc.value.status == 409


def test_rejected_section_not_committed(pile):
    seed_documents(pile["id"], SEED)
    run = service.start_run(pile["id"], kind="full", wait=True)
    for item in service.list_pending(run["id"]):
        approve = not (item["item_type"] == "section_update"
                       and item["payload"]["section_key"]
                       == "client-corvid-recruiting")
        service.decide_item(item["id"], approve=approve)
    service.resume_run(run["id"], wait=True)
    keys = {s["section_key"] for s in
            service.get_register(pile["id"])["sections"]}
    assert "client-corvid-recruiting" not in keys
    assert "client-brightline-health" in keys
