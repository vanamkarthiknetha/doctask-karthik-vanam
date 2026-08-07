# DocTask — The Analyst That Never Sleeps

An agentic document-analysis system that owns a growing pile of related,
disagreeing documents end to end: it understands them into a grounded
deliverable, examines them against a rules playbook, and stays alive as new
documents arrive — with a human gating every change, item by item.

**Domain (declared):** client service agreements of *Meridian Voice Systems*,
a fictional voice-AI platform — master service agreements, amendments, and
usage invoices. I work at a voice-AI company, so this is the paperwork I
actually understand: per-minute rates, monthly minute commitments, SLA
uptime, auto-renewal terms.

**Accepted formats (declared):** `.md` `.txt` `.html` `.docx` `.pdf`.
Anything else is refused with an honest 422, not silently skipped.

**Deliverable:** a *Vendor Obligations Register* — one section per client,
every value citing the exact place (file + character range) in a source
document. A claim with no verified supporting fact cannot be emitted — by
construction and by a database CHECK constraint.

All companies, contracts, and figures in the corpus are synthetic.

---

## Quickstart (one command)

```bash
docker compose up --build
```

Then open **http://localhost:8000/ui** and drive the whole loop from the
browser: **＋ New pile → upload documents** (drag-and-drop, or one click to
load the bundled sample corpus) **→ ▶ Run analysis → review each proposed
item → resume**. The pipeline stepper shows where the run is at any moment,
the header shows which LLM backend is live, and tabs are deep-linkable
(`/ui#review`, `/ui#register`, …). Works with **zero API keys**: the LLM
boundary falls back to a deterministic mock that replays recorded
extractions for the bundled corpus, and document rendering is skipped with
a logged decision.

Optional keys (put them in `.env`, see `.env.example`):

| Key | Enables | Notes |
|---|---|---|
| `GEMINI_API_KEY` | Live classification/extraction (Gemini 2.5 Flash) | free tier works |
| `ANTHROPIC_API_KEY` | Same, on Claude instead | alternative backend |
| `SUPERDOCS_API_KEY` | Styled `.docx`/`.pdf` register export via SuperDocs | uploads/exports are free ops |

### Drive a full analysis

```bash
# seed the demo pile, run, review (approve everything), commit, print register
docker compose exec api python scripts/demo_run.py
# or reject a finding to see per-item isolation:
docker compose exec api python scripts/demo_run.py --reject-rule R5
```

Or by hand against the API:

```bash
curl -X POST :8000/piles -d '{"name":"clients"}' -H 'content-type: application/json'
curl -X POST :8000/piles/<pile>/documents -F file=@corpus/seed/brightline-msa.md
curl -X POST :8000/piles/<pile>/runs -d '{"kind":"full","wait":true}' -H 'content-type: application/json'
curl :8000/runs/<run>/pending                # review queue
curl -X POST :8000/items/<item>/decision -d '{"approve":true}' -H 'content-type: application/json'
curl -X POST ":8000/runs/<run>/resume?wait=true"
curl :8000/piles/<pile>/register
```

### The watched location (stay alive)

Drop a file into `corpus/incoming/<pile-name>/` — plain Explorer/Finder
drag-and-drop works, the folder is bind-mounted into the container — and
the system produces a **focused update**: only the touched client's section
changes; everything else stays byte-identical and the commit records the
proof:

```bash
cp corpus/extra/brightline-amendment-2.md corpus/incoming/clients/
# a new update run appears at the review gate within ~2s — approve it in
# the UI, then check the provenance tab for the byte-identity record
```

### Tests — no live key required

```bash
docker compose up -d db
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # once
.venv/Scripts/python -m pytest tests -q
```

27 tests, ~40s. They test the claims, not the mocks: a hard process kill
mid-run with checkpoint resume (counting real boundary calls to prove no
rework), per-item gate isolation, a document that gives orders, unverifiable
quotes being dropped and escalated, two piles running concurrently, and the
byte-identity proof for focused updates.

### MCP server (machine-drivable, approval included)

```bash
python -m app.mcp_server     # stdio transport
```

13 tools: `create_pile, list_piles, add_document, run_analysis, get_run,
list_pending, approve_item, reject_item, resume_run, get_register,
get_audit_log, get_findings, get_costs`. Client config snippet is at the top
of `app/mcp_server.py`. Approval is an explicit operation — an agent (or a
script, or the React UI) drives the identical flow.

---

## How it works

```
            ┌─────────────────────────── LangGraph, checkpointed in Postgres ─┐
 documents  │ classify → extract → ground ─┐→ reconcile → compose → examine   │
 (5 formats)│              ▲ bounded retry ┘                          │       │
            │                                                      propose    │
            │                                              interrupt() = GATE │
            │                                                         │       │
            │                                     commit (byte-identity proof)│
            │                                                         │       │
            └──────────────────────────────── render (SuperDocs, free ops) ───┘
```

- **State lives in Postgres**, not process memory: documents, verified facts
  (with quote + char anchors), conflicts, register sections (content-hashed),
  findings, runs, stage events, the review queue, and a cost ledger. Kill the
  process anywhere; it continues from the last checkpoint, and finished work
  is never redone (`tests/test_kill_resume.py` counts the calls to prove it).
- **The LLM boundary is two calls** — `classify(text)` and `extract(text)` —
  behind a provider protocol (Gemini / Anthropic / deterministic mock).
  Model output is untrusted until the **ground** stage locates every quote
  verbatim in the source; unverifiable facts are retried twice, then dropped
  and escalated as findings. Extraction cannot invent a citation.
- **Reconciliation is deterministic code**, not vibes: the latest authoritative
  document (contract/amendment) wins cleanly (supersession, recorded); same-date
  disagreements and invoices restating different values are **conflicts** —
  surfaced to a human, never silently resolved.
- **Rules are data** (`rules/playbook.yaml`): three staged groups (terms →
  billing → register integrity) over seven rules. Every rule reports an
  outcome even when clean, so "no findings" on a clean corpus is a
  demonstrated result (`tests/test_pipeline.py::test_clean_corpus_honest_no_findings`).
- **The human gate is the graph's interrupt**: the run pauses at `propose`
  with one reviewable item per section update / conflict / finding. Items are
  decided one at a time; rejecting one touches nothing else; resume is
  refused while anything is undecided.
- **Injection defense is a feature**: prompts frame document text as data;
  a deterministic heuristic layer runs in parallel with the model's own
  signal; flagged documents become *findings* ("this document attempts to
  instruct the system") and their instructions are demonstrably not obeyed
  (`tests/test_injection.py` — the planted memo demands findings be deleted;
  they survive).
- **Costs are accounted per run, per stage**: LLM tokens and USD (per
  published prices), SuperDocs ops, latency. An update run measurably costs
  like an update: 2 boundary calls vs 20+ for a full run
  (`tests/test_focused_update.py::test_update_run_cost_is_update_sized`).

## The corpus and its planted defects

10 seed documents across 3 fictional clients (+ 2 clean documents for a
second run in `corpus/seed2/`, + 1 later amendment in `corpus/extra/` for
the watcher demo). Planted for the analyst to find:

| ID | Defect | Surfaced as |
|---|---|---|
| C1 | Jun invoice bills $0.45/min after Amendment 1 set $0.42 | conflict |
| C2 | Jul invoice says net-45; MSA says net-30 | conflict |
| C3 | Invoice prints $0.41/min; MSA says $0.38 | conflict |
| F1 | 24-month auto-renewal (playbook caps at 12) | finding R1 |
| F2 | Invoice total ≠ minutes × its own stated rate | finding R4 |
| F3 | Memo instructs AI systems to approve invoices / delete findings | finding R6 |
| F4 | Invoice bills below the monthly minimum commitment | finding R5 |

`scripts/make_corpus.py` generates the documents AND the mock fixtures from
one source of truth, then re-ingests its own output and verifies every quote
anchors — the corpus cannot drift from the fixtures.

## Decisions and honest cuts

- **Live LLM is Gemini** (`gemini-2.5-flash`) because that's the key I have.
  The Anthropic provider is implemented behind the same protocol but is
  **not live-tested**; Gemini and the mock are the tested paths.
- **pgvector is not used.** The plan sketched embeddings for impact analysis;
  deterministic provenance links (new document → its entity → its sections)
  answer the same question exactly, so similarity search would be decoration.
  The image ships with the extension available if a future corpus needs it.
- **SuperDocs is the presentation layer, not the update mechanism.** Focused
  updates happen at the section level in Postgres (hash-proven); each commit
  re-renders via upload + export, which are free operations — the ops ledger
  stays at 0 for rendering. Using chat-edits per section would spend ops to
  prove something the hashes already prove. Two export-fidelity rough edges
  found on the way (inline-styles-only; `background-color` longhand) are
  handled in the renderer and reported in the parent project's bug log.
- **The review UI shows register sections as monospace markdown**, not
  rendered HTML — reviewers diff content; the styled artifact is the
  exported docx/pdf.
- **One active run per pile** (409 otherwise): updates queue in the watched
  folder rather than interleaving. Two *piles* run concurrently just fine.

## Repo map

```
app/            the system (stages, graph, service, api, mcp, providers)
corpus/seed     the bundled corpus (10 docs, defects planted)
corpus/seed2    a second, clean document set (different clients)
corpus/extra    the later amendment used for the focused-update demo
rules/          the playbook (rules as data)
scripts/        corpus generator, end-to-end demo driver
tests/          the claims, tested without any live key
ui/             React review UI (servable by the API at /ui)
TASK.md         the task restated + behavior-by-behavior evidence
PROGRESS.md     build log: assumptions and decisions as they were made
```
