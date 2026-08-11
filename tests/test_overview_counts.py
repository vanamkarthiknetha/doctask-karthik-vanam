"""The register's overview must report what the analysis found, not the
state of the review queue.

Two failure modes this pins down:

1. Rules run in `examine`, which used to happen AFTER the overview was
   composed — so a run that raised three findings still printed "0", a false
   statement in a deliverable whose whole premise is that it never states
   anything it cannot support.
2. Counting only undecided work makes the number structurally zero in every
   EXPORTED document, because export happens after the gate is drained. A
   count that can only ever read zero tells a reader nothing.
"""
from conftest import SEED, full_run_approved, seed_documents

from app import db, service


def _overview(pile_id: str) -> str:
    return next(s["content_md"] for s in
                service.get_register(pile_id)["sections"]
                if s["section_key"] == "overview")


def test_overview_counts_findings_raised_by_this_run(pile):
    """The seed corpus plants rule violations; the overview composed in the
    same run must count them rather than the zero it saw before they ran."""
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])

    raised = db.one("SELECT count(*) AS n FROM findings WHERE pile_id=%s",
                    (pile["id"],))["n"]
    assert raised > 0, "seed corpus should raise findings"
    assert f"Issues raised against the rules: {raised}" in _overview(pile["id"])


def test_found_counts_survive_the_review_the_outstanding_ones_settle(pile):
    """The outstanding counts are "as of this analysis": the overview is
    composed before the gate, because composing it afterwards would put text
    into the register that nobody approved. So they read non-zero in the run
    that raises them and settle to zero on the next run — while the FOUND
    counts persist, which is what a reader of an exported register needs."""
    seed_documents(pile["id"], SEED)
    full_run_approved(pile["id"])
    conflicts = db.one("SELECT count(*) AS n FROM conflicts WHERE pile_id=%s",
                       (pile["id"],))["n"]
    findings = db.one("SELECT count(*) AS n FROM findings WHERE pile_id=%s",
                      (pile["id"],))["n"]
    assert findings > 0

    first = _overview(pile["id"])
    assert f"Issues raised against the rules: {findings}" in first
    assert "(0 awaiting review)" not in first, \
        "the run that raised them composed before they were decided"

    # A second run over the same documents: everything is decided by now.
    full_run_approved(pile["id"])
    second = _overview(pile["id"])
    assert "(0 still open)" in second
    assert "(0 awaiting review)" in second
    # The analysis's findings are still reported — not erased by approval.
    assert f"Disagreements between documents: {conflicts}" in second
    assert f"Issues raised against the rules: {findings}" in second
