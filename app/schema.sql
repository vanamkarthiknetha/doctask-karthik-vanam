-- DocTask schema. All state lives here: runs survive process kills, and
-- concurrent runs stay isolated because every row is scoped to a pile.

CREATE TABLE IF NOT EXISTS piles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    rules_yaml  TEXT,  -- pile-specific playbook the user handed us; NULL = system default
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotent for pre-existing databases (docker volumes persist across runs);
-- CREATE TABLE IF NOT EXISTS above does not add columns to an existing table.
ALTER TABLE piles ADD COLUMN IF NOT EXISTS rules_yaml TEXT;

CREATE TABLE IF NOT EXISTS documents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pile_id           UUID NOT NULL REFERENCES piles(id) ON DELETE CASCADE,
    filename          TEXT NOT NULL,
    format            TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    raw_text          TEXT NOT NULL,
    doc_class         TEXT,            -- contract | amendment | invoice | unknown
    entity            TEXT,            -- client the document belongs to
    doc_date          DATE,
    class_confidence  REAL,
    status            TEXT NOT NULL DEFAULT 'ingested',  -- ingested|classified|extracted|quarantined
    injection_flagged BOOLEAN NOT NULL DEFAULT FALSE,
    added_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pile_id, sha256)
);

-- A fact is only admissible with an exact, verified quote from its source.
CREATE TABLE IF NOT EXISTS facts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pile_id     UUID NOT NULL REFERENCES piles(id) ON DELETE CASCADE,
    doc_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    run_id      UUID NOT NULL,
    entity      TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    quote       TEXT NOT NULL CHECK (length(quote) > 0),
    char_start  INT  NOT NULL,
    char_end    INT  NOT NULL,
    superseded_by UUID REFERENCES facts(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conflicts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pile_id     UUID NOT NULL REFERENCES piles(id) ON DELETE CASCADE,
    run_id      UUID NOT NULL,
    entity      TEXT NOT NULL,
    key         TEXT NOT NULL,
    fact_ids    UUID[] NOT NULL,
    detail      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',   -- open | acknowledged | rejected
    decided_by  TEXT,
    feedback    TEXT,
    decided_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Register sections are content-hashed: untouched sections keep their hash,
-- which is the byte-identity proof for focused updates.
CREATE TABLE IF NOT EXISTS sections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pile_id         UUID NOT NULL REFERENCES piles(id) ON DELETE CASCADE,
    section_key     TEXT NOT NULL,
    title           TEXT NOT NULL,
    content_md      TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    updated_by_run  UUID,
    updated_reason  TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pile_id, section_key)
);

-- Schema-enforced grounding: a claim cannot exist without at least one fact.
CREATE TABLE IF NOT EXISTS claims (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_id  UUID NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    fact_ids    UUID[] NOT NULL CHECK (cardinality(fact_ids) > 0)
);

CREATE TABLE IF NOT EXISTS findings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pile_id     UUID NOT NULL REFERENCES piles(id) ON DELETE CASCADE,
    run_id      UUID NOT NULL,
    rule_id     TEXT NOT NULL,
    severity    TEXT NOT NULL,
    entity      TEXT,
    message     TEXT NOT NULL,
    doc_id      UUID REFERENCES documents(id) ON DELETE CASCADE,
    quote       TEXT,
    char_start  INT,
    char_end    INT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    decided_by  TEXT,
    feedback    TEXT,
    decided_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pile_id      UUID NOT NULL REFERENCES piles(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,               -- full | update
    status       TEXT NOT NULL DEFAULT 'running',
    -- running | awaiting_review | committing | completed | failed
    thread_id    TEXT NOT NULL UNIQUE,        -- LangGraph checkpoint thread
    trigger_doc  UUID,
    error        TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS stage_events (
    id       BIGSERIAL PRIMARY KEY,
    run_id   UUID NOT NULL,
    stage    TEXT NOT NULL,
    decision TEXT NOT NULL,
    detail   JSONB NOT NULL DEFAULT '{}',
    ts       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The human gate: one row per reviewable item; deciding one never touches the rest.
CREATE TABLE IF NOT EXISTS pending_items (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID NOT NULL,
    pile_id     UUID NOT NULL REFERENCES piles(id) ON DELETE CASCADE,
    item_type   TEXT NOT NULL,     -- section_update | conflict | finding
    ref_id      UUID,              -- findings.id / conflicts.id when applicable
    payload     JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    decided_by  TEXT,
    feedback    TEXT,
    decided_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cost_ledger (
    id            BIGSERIAL PRIMARY KEY,
    run_id        UUID,
    pile_id       UUID,
    stage         TEXT NOT NULL,
    provider      TEXT NOT NULL,   -- anthropic | mock | superdocs
    model         TEXT,
    input_tokens  INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    superdocs_ops INT NOT NULL DEFAULT 0,
    usd           NUMERIC(12,6) NOT NULL DEFAULT 0,
    latency_ms    INT NOT NULL DEFAULT 0,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Schema-enforced: at most one active run per pile, ever — not just a
-- read-then-write check in Python, which races under concurrent start_run
-- calls on the same pile. Status transitions are UPDATEs on the same row,
-- so a run progressing through its own active statuses never conflicts
-- with itself; only a genuinely second concurrent run does.
CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_one_active_per_pile
    ON runs (pile_id) WHERE status IN ('running', 'awaiting_review', 'committing');

CREATE INDEX IF NOT EXISTS idx_documents_pile ON documents(pile_id);
CREATE INDEX IF NOT EXISTS idx_facts_pile ON facts(pile_id, entity, key);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_pending_run ON pending_items(run_id, status);
CREATE INDEX IF NOT EXISTS idx_events_run ON stage_events(run_id);
CREATE INDEX IF NOT EXISTS idx_costs_run ON cost_ledger(run_id);
