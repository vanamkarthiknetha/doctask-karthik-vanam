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
| 4 | Machine-drivable, approval an operation | `app/service.py` is the single operations layer; REST (`app/api.py`) and **MCP server** (`app/mcp_server.py`, 13 tools incl. `approve_item`/`reject_item`) wrap it | `scripts/demo_run.py` drives the whole flow programmatically; MCP config snippet in the module docstring |
| 5 | Never bluffs | Facts require a verbatim quote located in the source (`ground` stage); claims require fact_ids (schema CHECK); unverifiable extractions are DROPPED and escalated, not guessed; clean rule stages report "clean" explicitly | `tests/test_grounding.py`, `tests/test_pipeline.py::test_every_claim_cites_verified_facts`, `::test_clean_corpus_honest_no_findings` |
| 6 | Stranger runs it in minutes | `docker compose up --build` → UI at :8000/ui, keys optional (mock mode) | README quickstart |
| 7 | Real tests without a live key | mock at the LLM boundary replays fixtures generated from the same source of truth as the corpus; SuperDocs disabled in tests | 20 tests, `pytest -q`, no key present |
| 8 | Prompt-injection safe | prompts frame documents as data; deterministic heuristics + model signal; flagged docs become findings; instructions demonstrably not followed | `tests/test_injection.py` — the memo demands Halcyon findings be deleted and invoices approved; findings survive, conflict stays open, memo contributes zero facts |
| 9 | Concurrent runs isolated | every row pile-scoped; per-pile single-active-run guard (409); checkpointer pool | `tests/test_concurrency.py` — two piles in parallel with zero cross-talk; busy pile refused |
| 10 | Cost/time accounting | `cost_ledger`: per run, per stage — provider, model, tokens, USD, SuperDocs ops, latency; `GET /runs/{id}/costs`, UI costs tab | `tests/test_focused_update.py::test_update_run_cost_is_update_sized` — an update costs like an update (2 calls vs 20+) |

## Movement 3 specifics

- Watched location: `WATCH_DIR/<pile-name>/` (`app/watcher.py`), files moved
  to `processed/`/`failed/` after handling; busy pile → file waits (queued).
- Focused update: impact = entities of the arriving document; only their
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
