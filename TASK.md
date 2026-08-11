# TASK — The Analyst That Never Sleeps

Build a system that owns a pile of related, disagreeing, growing documents:

1. **Understand** — ingest mixed formats, classify, extract the facts that
   matter, notice disagreements, produce ONE grounded deliverable where every
   claim traces to the exact place in a source.
2. **Examine** — check sources + deliverable against user-supplied rules in
   stages; findings carry exact source pointers; a clean corpus yields an
   honest "no findings" report.
3. **Stay alive** — a watched location; each arrival produces a focused
   update; untouched parts stay byte-identical and the system can prove it;
   contradictions surfaced, never silently resolved.

A human gates everything, item by item.

## Behavior-by-behavior evidence

| # | Behavior | Where it lives | Proof |
|---|---|---|---|
| 1 | Visible stages, real decisions | `app/stages.py`, `app/graph.py` — every node writes decisions to `stage_events`; retry/quarantine/escalate change the path (conditional edge ground→extract, bounded; quarantine on low-confidence classify) | timeline in `GET /runs/{id}` and the UI; `tests/test_grounding.py` (retry→escalate path) |
| 2 | Survives being stopped | LangGraph + Postgres checkpointer; idempotent nodes; `continue_incomplete_runs()` on startup | `tests/test_kill_resume.py` — hard `TerminateProcess` mid-run, resume in a fresh process, classify-call count proves zero rework |
| 3 | Human gate, item by item | `pending_items` rows; `decide_item` touches exactly one row; resume refused while any item is pending | `tests/test_gate.py` — reject one finding, the rest survive; rejected section not committed; double-decide refused |
| 4 | Machine-drivable, approval an operation | `app/service.py` is the single operations layer; REST (`app/api.py`) and **MCP server** (`app/mcp_server.py`, 16 tools incl. `approve_item`/`reject_item`/`set_pile_rules`) wrap it | `scripts/demo_run.py` drives the whole flow programmatically; MCP config snippet in the module docstring |
| 5 | Never bluffs | Facts require a verbatim quote located in the source (`ground` stage); claims require fact_ids (schema CHECK); unverifiable extractions are DROPPED and escalated, not guessed; clean rule stages report "clean" explicitly | `tests/test_grounding.py`, `tests/test_pipeline.py::test_every_claim_cites_verified_facts`, `::test_clean_corpus_honest_no_findings` |
| 6 | Stranger runs it in minutes | `docker compose up --build` → UI at :8000/ui drives the full loop clickably (new pile, drag-and-drop upload, one-click sample corpus, run, item-by-item review, resume), keys optional (mock mode) | README quickstart |
| 7 | Real tests without a live key | mock at the LLM boundary replays fixtures generated from the same source of truth as the corpus; SuperDocs disabled in tests | 27 tests, `pytest -q`, no key present |
| 8 | Prompt-injection safe | prompts frame documents as data; deterministic heuristics + model signal; flagged docs become findings; instructions demonstrably not followed | `tests/test_injection.py` — the memo demands Halcyon findings be deleted and invoices approved; findings survive, conflict stays open, memo contributes zero facts |
| 9 | Concurrent runs isolated | every row pile-scoped; per-pile single-active-run guard (409); checkpointer pool | `tests/test_concurrency.py` — two piles in parallel with zero cross-talk; busy pile refused |
| 10 | Cost/time accounting | `cost_ledger`: per run, per stage — provider, model, tokens, USD, SuperDocs ops, latency; `GET /runs/{id}/costs`, UI costs tab | `tests/test_focused_update.py::test_update_run_cost_is_update_sized` — an update costs like an update (2 calls vs 20+) |

## Movement 2 specifics

- **Rules are literally user-supplied, per pile** — not just a config file
  the engineer authored once for the whole deployment. `PUT /piles/{id}/rules`
  (+ MCP `set_pile_rules`, + the UI's Rules tab) stores a staged YAML
  playbook on the pile itself; `GET`/`DELETE` read it back and revert to the
  system default. A pile with no rules of its own falls back to
  `rules/playbook.yaml`.
- Validated, not trusted: every rule must name a check the engine actually
  implements (`app/rules_engine.py::validate_playbook`); an unrecognized
  check is refused with a 422 naming exactly which one, rather than being
  silently dropped at run time — the same "never bluffs" boundary the rest
  of the system holds extraction to.
- The examine stage records which source it used (`pile` or `default`) in
  that run's stage events — visible in the audit trail, not just inferable
  from which findings showed up.
- **Proof, not assertion:** `tests/test_pile_rules.py::test_pile_rules_change_what_a_real_run_flags`
  runs the same corpus through two piles with two different active
  playbooks and asserts the finding sets differ — confirming the pile's
  rules are what a real run enforces, not just what the endpoint echoes
  back.
- **Honest cut:** rules must be expressed as staged YAML against the known
  check registry. This system does not accept an arbitrary prose document
  (a PDF style guide) and have an LLM derive structured checks from it —
  that risks an invented rule the model got wrong looking identical to one
  it got right, which is exactly what behavior 5 forbids. Reasoning logged
  in README's "Decisions and honest cuts".

## Movement 3 specifics

- Watched location: `WATCH_DIR/<pile-name>/` (`app/watcher.py`), files moved
  to `processed/`/`failed/` after handling; busy pile → file waits (queued).
  Everything present in one sweep is one arrival: a bulk drop is a single
  update run, not one run (and one human gate) per file
  (`tests/test_watcher.py::test_batch_arrival_is_a_single_update_run`); an
  unreadable file goes to `failed/` without costing the rest their run.
- The same focused path is reachable from the UI: with documents waiting
  unanalyzed, the control bar offers "Analyze N new" (`kind=update` over
  exactly those documents) beside "Re-analyze all". Upload never auto-runs —
  batching and cost stay the human's call; the watched folder is the
  unattended path.
- Focused update: impact = entities of the arriving document(s); only their
  sections recompose. `tests/test_focused_update.py` asserts the other
  sections' hashes are **byte-identical** and that the commit event records
  the before/after hash per section — the proof, not just the property.
- "What changed, when, because of which source": `GET /piles/{id}/audit` —
  per-section run/kind/trigger-document, per-fact anchors + supersession,
  conflicts with their resolutions. Surfaced in the UI provenance tab.

## Second run on a different document set

`corpus/seed2/` (Juniper Logistics — different client, clean documents,
same declared formats/domain) exercised by
`tests/test_pipeline.py::test_clean_corpus_honest_no_findings` and runnable
via `python scripts/demo_run.py --pile juniper --dir corpus/seed2`.
