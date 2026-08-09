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
- **P20 · Per-pile rules upload (2026-08-07) — closing a real gap, not a
  cosmetic one.** TASK.md and the original plan both said "user-supplied
  rules," but the rules engine only ever read one file
  (`rules/playbook.yaml`) from a fixed path — an engineer-authored config,
  not something a user (or their agent) could hand the system per pile. The
  brief's movement 2 line is explicit: "the user hands it the rules they
  care about." Fixed at the schema level: `piles.rules_yaml` (nullable —
  NULL means "use the system default"), `GET/PUT/DELETE /piles/{id}/rules`
  (+ matching MCP tools + a UI Rules tab), validated against the engine's
  own check registry before being stored so an unenforceable rule is
  refused at upload time with the specific reason, never silently dropped
  at run time. `examine_node` resolves the active playbook per run and logs
  which source it used. Deliberately NOT built: deriving rules from a
  freeform prose document (upload a PDF style guide, let an LLM infer
  checks) — that trades a bounded, auditable input for one where a wrong
  inferred rule is indistinguishable from a right one, which is precisely
  what behavior 5 (never bluffs) rules out. Proof it actually changes
  pipeline behavior, not just what the endpoint reports back:
  `tests/test_pile_rules.py::test_pile_rules_change_what_a_real_run_flags`
  — same corpus, two piles, two active playbooks, two different finding
  sets, and the run's own audit trail says which playbook produced which.
- **P21 · Rules tab: raw YAML textarea replaced with a guided builder
  (2026-08-07).** P20 shipped `PUT /piles/{id}/rules`, satisfying the letter
  of "the user hands it the rules" — but the first UI for it was a bare
  YAML textarea, which quietly re-imposed an engineer-only bar on exactly
  the audience the brief names (a compliance officer, a contract manager).
  Inconsistent with the rest of this UI, which never assumes technical
  fluency (`RULE_TITLES`, `KEY_LABELS`, `describeEvent`, the glossary
  panel). Fixed with a **🧩 Builder** view as the default: a plain-English
  form (check type → fields → severity → description) that renders rules as
  removable cards; the assembled YAML stays visible read-only underneath
  (the existing `raw-toggle`/"technical detail" disclosure pattern already
  used for register content and stage-event JSON), so nothing is hidden,
  it's just never required reading. A **📝 Raw YAML** toggle keeps the
  original textarea for anyone who wants to hand-edit or paste a full
  playbook. `js-yaml` (new UI dependency — `yaml.dump`/`yaml.load`) keeps
  the two views in sync in both directions; switching back to Builder mode
  after a raw-YAML edit that fails to parse shows the parse error inline
  and stays in YAML mode rather than silently discarding the edit. The
  builder is closed-form by construction: it only ever emits the 7 checks
  `app/rules_engine.py` implements (`CHECK_DEFS` in `App.jsx` mirrors
  `CHECKS` in `rules_engine.py`), so it cannot promise a rule the engine
  can't enforce — same trust boundary as P20's upload-time validation.
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

- **P22 - Review UI restyled on Tailwind v4 + shadcn/ui (vendored), zero
  behavior change.** The hand-rolled CSS had hit its ceiling, and the review
  interface is the face of the human gate - presentation quality is part of
  what this round grades. Swap: shadcn/ui components vendored as JSX under
  `ui/src/components/ui/` (button, card, badge, tabs, select, input,
  textarea, table, alert, sonner toasts), lucide icons replacing emoji,
  Inter bundled via @fontsource (no CDN at runtime - the container still
  works fully offline), and a stat-tile Cost tab. Every piece of application
  logic - the 2.5s polling refresh, run/pile selection, decision posting,
  rule-builder YAML round-tripping, hash deep links - was transcribed
  unchanged; only presentation moved. Verified the way A16 demands: rebuilt
  the Docker image and screenshotted all eight tabs against the live
  backend, which caught the one real defect (the run selector truncating
  "needs review" to "nee") before commit. One constraint Radix imposes: a
  select item may not carry an empty-string value, so the "no runs yet"
  state is a disabled placeholder trigger rather than an empty option -
  identical visible behavior. Two of the verified screenshots now live in
  `screenshots/` and anchor the README.

- **P23 - Exports were being written into the container's ephemeral layer;
  fixed with a bind mount (found by asking "where do the files actually
  go?").** docker-compose declared an `exports` named volume at
  /data/exports but never set EXPORT_DIR, so the render node fell back to
  REPO_ROOT/exports = /srv/exports inside the container: exports were
  invisible from the host and deleted on every container recreation, while
  the intended volume sat empty. The render event still said "exported" -
  true inside the container, useless to a human, and against the spirit of
  behavior 5. Fix: EXPORT_DIR=/data/exports plus a HOST BIND MOUNT
  (./exports:/data/exports), not the named volume - exported .docx/.pdf are
  exactly the files a human opens in Word, so they belong in a browsable
  folder, same pattern as corpus/incoming. Verified end to end: a file
  touched at /data/exports inside the container appeared in .\exports\ on
  the host; the two registers trapped in the old container layer were
  docker-cp'd out before recreation. Same audit found the same class of
  issue one more time: the default playbook was baked into the image, so a
  host edit of rules/playbook.yaml silently did nothing until a rebuild -
  now bind-mounted read-only with RULES_FILE=/data/rules/playbook.yaml
  ("configuration over code": a rule edit is a data change, not a rebuild).
  Volume policy now stated in the compose comments: named volume for
  machine state no human browses (Postgres), bind mounts for files humans
  exchange with the system (incoming docs, exports, rules), container layer
  for nothing that matters. Remaining known gap, deliberately not fixed
  here: upload still reads the whole file into memory (api.py
  `await file.read()`) - honest limitation for very large files, noted for
  the write-up.

- **P24 - Classify prompt made vendor-agnostic for the demo-call corpus
  (2026-08-09).** A fresh demo pile (project-root `demo-corpus/`, clients
  Oakline Retail Group / Harborlight Payments, vendor Kestrel Voice
  Systems) uses a different fictional provider than the seed corpus's
  Meridian Voice Systems. The classify system prompt in both live
  providers hardcoded "not the provider Meridian Voice Systems" as the
  entity disambiguator; replaced with a structural definition (the party
  labeled "Client" / the "Bill to" party, never the issuing
  provider/vendor). Why: entity selection should not depend on knowing
  the vendor's name - same trust-boundary philosophy as P14/P15, applied
  to the prompt itself. Offline tests unaffected (mock replays fixtures,
  prompts unused). MUST rehearse one full live run on the demo corpus
  before the call: this prompt path is live-only and was previously
  live-verified only with the Meridian wording.
