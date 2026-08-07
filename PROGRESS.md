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
