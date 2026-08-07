"""End-to-end demo/smoke driver. Runs the whole flow through the service
layer exactly as a machine caller would: create pile -> add documents ->
run analysis -> review the gate item by item -> resume -> inspect outputs.

Usage:
  python scripts/demo_run.py                # seed corpus, approve everything
  python scripts/demo_run.py --pile NAME --dir corpus/seed2
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app import db, service  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pile", default="meridian-clients")
    ap.add_argument("--dir", default=str(REPO / "corpus" / "seed"))
    ap.add_argument("--reject-rule", default=None,
                    help="reject findings whose rule_id contains this string")
    args = ap.parse_args()

    db.init_schema()
    pile = service.create_pile(args.pile)
    print(f"pile {pile['name']} = {pile['id']}")

    for path in sorted(Path(args.dir).iterdir()):
        if path.suffix.lower() in {".md", ".txt", ".html", ".docx", ".pdf"}:
            doc = service.add_document_bytes(pile["id"], path.name,
                                             path.read_bytes())
            print(f"  added {doc['filename']} ({doc['format']})"
                  + (" [duplicate]" if doc.get("duplicate") else ""))

    run = service.start_run(pile["id"], kind="full", wait=True)
    run = service.get_run(run["id"])
    print(f"run {run['id']} -> {run['status']}")

    items = service.list_pending(run["id"])
    print(f"gate: {len(items)} items to review")
    for item in items:
        p = item["payload"]
        label = {
            "section_update": lambda: f"section {p['section_key']}",
            "conflict": lambda: f"CONFLICT {p['entity']}/{p['key']}: {p['detail'][:80]}",
            "finding": lambda: f"FINDING {p['rule_id']} [{p['severity']}]: {p['message'][:80]}",
        }[item["item_type"]]()
        reject = (args.reject_rule and item["item_type"] == "finding"
                  and args.reject_rule in p["rule_id"])
        service.decide_item(item["id"], approve=not reject,
                            decided_by="demo-script",
                            feedback="rejected by demo flag" if reject else None)
        print(f"  {'REJECT' if reject else 'approve'}: {label}")

    run = service.resume_run(run["id"], wait=True)
    run = service.get_run(run["id"])
    print(f"run resumed -> {run['status']}")

    reg = service.get_register(pile["id"])
    print("\n===== REGISTER =====")
    print(reg["markdown"])
    costs = service.get_costs(pile_id=pile["id"])
    print("===== COSTS =====")
    print(json.dumps(costs["total"], indent=2))
    print("\nstage timeline:")
    for e in run["events"]:
        print(f"  {e['stage']:24s} {e['decision']}")


if __name__ == "__main__":
    main()
