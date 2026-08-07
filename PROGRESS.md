# PROGRESS — build log (assumptions and decisions as they were made)

Format: id · decision/assumption · why · how to revisit.

- **P1 · Domain = fictional voice-AI vendor's client agreements.** Chosen to
  match the author's day job (voice-AI platform engineering) while keeping
  the assessment's proven structure (contracts + amendments + invoices →
  obligations register). All entities synthetic; the fictional provider
  "Meridian Voice Systems" is not a real company.
- **P2 · Facts anchor to whitespace-normalized text.** Every reader collapses
  whitespace runs to single spaces so a quote anchors identically across
  md/html/docx/pdf renderings of the same sentence (PDF line-wrapping was the
  forcing case). Anchors (char ranges) always refer to normalized text.
- **P3 · Corpus and mock fixtures generated from one source of truth.**
  `scripts/make_corpus.py` writes the documents, re-ingests its own output,
  verifies every fixture quote anchors, and only then writes the fixtures.
  Generation fails loudly if a quote drifts.
- **P4 · State in Postgres, graph state carries IDs only.** LangGraph
  checkpoints stay small; nodes are idempotent against the database because
  an interrupted node replays from its start after a crash.
- **P5 · Reconciliation and composition are deterministic code.** The LLM
  classifies and extracts; supersession, conflict detection, and register
  rendering are auditable rules. A claim literally cannot be emitted without
  a verified fact (renderer takes fact rows as input; schema CHECK backs it).
- **P6 · Live LLM = Gemini (user has no Anthropic key).** Provider protocol
  kept pluggable; `auto` = Anthropic key → Anthropic, else Gemini key →
  Gemini, else mock. The Anthropic provider is present but NOT live-tested —
  honest status, not a claim.
- **P7 · Injection defense is layered and reported.** Deterministic regex
  heuristics OR the model's own `instruction_like` signal flag a document;
  the flag becomes a finding via playbook rule R6. The planted memo's
  instructions are asserted NOT to have been followed (test).
- **P8 · pgvector cut.** Impact analysis is exact via provenance links
  (arriving doc → entity → sections); similarity search adds nothing at this
  corpus scale. Extension available in the image if ever needed.
- **P9 · SuperDocs = presentation layer; renders are free.** Upload + export
  bill zero ops (verified in Task 2); byte-identity is proven at the
  section-hash layer, so chat-edit-per-section would spend ops to duplicate
  an existing proof. Renderer works around two export-fidelity rough edges
  found by LOOKING at the rasterized output: `<style>` blocks are ignored
  (inline styles only) and `background:` shorthand is dropped
  (`background-color` longhand required). Both reported upstream.
- **P10 · One active run per pile.** Second `start_run` on a busy pile → 409;
  the watcher leaves files in place and retries next sweep, so updates queue
  instead of interleaving. Cross-pile concurrency is unrestricted and tested.
- **P11 · Overview section changes on most runs** (it counts documents/open
  conflicts/pending findings). Byte-identity claims are made about client
  sections; the overview honestly reports its own change reason.
- **P12 · mcp 2.0 renamed FastMCP → `mcp.server.mcpserver.MCPServer`**;
  same decorator surface. Pinned by requirements freeze.
- **P13 · Kill/resume test kills between stages deterministically** by
  waiting for the first `extract` call in the mock's call log, guaranteeing
  classification finished — the assertion "zero additional classify calls
  after resume" is then exact, not probabilistic.
- **P15 · Key normalization + whitelist (found by the second LIVE run).**
  gemini-2.5-flash-lite decorated fact keys with the prompt's category
  labels ("contracts/amendments/per_minute_rate"), so verified facts existed
  under names nothing joins on — contract terms silently vanished from the
  register. Fix at the same trust boundary as quote grounding: keys are
  normalized (last path segment, lowercased) and must land in the known-key
  whitelist or the fact is dropped with a logged `unknown-keys-dropped`
  event. The extraction prompt now enumerates the ten allowed key strings
  flat, with an explicit "no prefix/suffix/category label" instruction.
  Running theme of this project, now three layers deep: quotes, entities,
  keys — verify every joinable string a model returns.
- **P16 · Live verification status — now COMPLETE (2026-08-07, fresh key).**
  Two full live runs completed end to end earlier (pipeline, grounding 10/10
  verbatim, gate, commit, SuperDocs render; ~$0.002/run), surfacing and
  fixing entity drift (P14) and key drift (P15) and confirming R1/R4/R6.
  With a new Gemini key, a third full live run plus a live watcher update
  run confirmed the remainder: **C1, C2*, C3 conflicts and R5 all fire
  live** (*C2 fired after the P18 fix below — its first live absence is what
  exposed P18). Full run: ~21 calls / $0.019; focused update: 2 calls /
  $0.0012 with byte-identity proven for untouched sections.
- **P18 · Contract-key restatement conflicts (found by the third LIVE run).**
  Gemini stated the July invoice's terms as `payment_terms=net-45` — the
  CONTRACT key — instead of `billed_terms`. Reconcile correctly refused to
  let it become effective (invoices never supersede authoritative docs) but
  the counterpart-conflict pass only watched `billed_*` keys, so the planted
  C2 conflict silently vanished. Fix: a contract-term fact from a
  non-authoritative document now hits the same comparison as its `billed_*`
  counterpart, and the same disagreement stated under both spellings dedupes
  to one conflict. Unit-tested against the exact live shape; then confirmed
  live (C2 fired on the next update run). Fourth instance of the project
  theme: verify every joinable string a model returns — including which KEY
  it chose to put a value under.
- **P19 · Latest-invoice ordering by document date, not period prose.** The
  register's "Latest invoice" line picked the newest invoice by string-max
  of the model-returned `invoice_period`. Mock fixtures said "2026-07"
  (sortable); the live model said "July 2026", and "July" < "June" as a
  string, so June was presented as latest. Fix: order by the document's
  schema-validated ISO `doc_date` (the same authority supersession already
  uses); the period text is display only. Unit-tested with the live strings.
- **P17 · The UI drives the full loop (2026-08-07).** Added pile creation,
  drag-and-drop upload, one-click sample-corpus load (`POST
  /piles/{id}/documents/sample`, sets listed at `GET /corpus`), a Run
  analysis button, a pipeline stepper with live stage read-out, an LLM
  provider badge from `/health`, deep-linkable tabs (`/ui#review`), and
  auto-follow of the newest run (the review tab opens itself when a run
  reaches the gate). The watch folder became a host bind mount
  (`./corpus/incoming`) so Explorer drag-and-drop feeds the watcher.
  Ingestion via REST/watcher/MCP is unchanged — the UI is a client of the
  same service layer, not a new path.
- **P14 · Entity canonicalization (found by the first LIVE run, not the
  mock).** Gemini spelled the same client three ways across documents
  ("Halcyon" / "Halcyon Support Desk Ltd" / …), fragmenting entity-keyed
  joins: conflicts and cross-document rules silently missed. Deterministic
  fix in `classify_node`: legal-suffix tokens stripped, and a name that
  token-prefixes an already-known entity in the pile unifies to it
  (first-seen display wins); every canonicalization is a logged stage event.
  Assumption accepted knowingly: within one pile, a token-prefix name is the
  same client (two real clients named "Acme" and "Acme East" in one pile
  would wrongly merge — the fix for that day is human resolution, not
  silent guessing).
