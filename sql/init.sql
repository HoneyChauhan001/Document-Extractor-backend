-- =============================================================================
-- Doc-Extractor: PostgreSQL initialisation script
-- Run once against a fresh database:
--   psql $DATABASE_URL -f sql/init.sql
--
-- Design decisions:
--   • UUIDs as PKs — avoids integer ID leakage, safe to expose in APIs.
--   • gen_random_uuid() requires the pgcrypto extension (enabled below).
--   • All timestamps stored as TIMESTAMPTZ (timezone-aware) to avoid TZ bugs.
--   • UNIQUE constraints match canonical contracts in copilot-instructions.md.
--   • CHECK constraints enforce allowed enum-like values at DB level for safety.
-- =============================================================================

-- pgcrypto is a built-in Postgres extension providing gen_random_uuid().
-- Safe to run multiple times (IF NOT EXISTS).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- TABLE: jobs
-- Represents one upload session.  A job contains 1–5 documents.
-- =============================================================================
CREATE TABLE IF NOT EXISTS jobs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Lifecycle status of the job as a whole.
    -- PENDING      → job row created, files not yet stored
    -- INGESTED     → all files saved to disk and documents rows created
    -- PARTIAL_FAILURE → at least one file failed but others succeeded
    -- EXTRACTING   → extraction pipeline running (future)
    -- DONE         → all pipeline stages complete (future)
    -- FAILED       → job could not be started at all
    status          TEXT        NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','INGESTED','PARTIAL_FAILURE',
                                      'EXTRACTING','DONE','FAILED')),

    -- How many document files were submitted with this job.
    document_count  INT         NOT NULL DEFAULT 0,

    -- Arbitrary JSON for future extensibility (e.g., requester metadata, batch id).
    -- Stored as JSONB for efficient querying.
    metadata        JSONB,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- TABLE: documents
-- One row per uploaded file within a job.
-- Files are stored on disk; this row records the path and processing status.
-- =============================================================================
CREATE TABLE IF NOT EXISTS documents (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Foreign key to the parent job.  Cascade delete means if a job is deleted
    -- all its document records are cleaned up automatically.
    job_id          UUID        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,

    -- Original filename as supplied by the uploader (sanitised at app layer).
    filename        TEXT        NOT NULL,

    -- Absolute or relative path on disk where the file bytes are stored.
    -- NULL if the file failed to save.
    file_path       TEXT,

    -- 'pdf' or 'docx' — determined by file extension at ingestion time.
    file_type       TEXT        CHECK (file_type IN ('pdf', 'docx')),

    -- Per-document processing status.
    -- INGESTED     → file saved to disk successfully
    -- EXTRACTING   → extraction running (future)
    -- EXTRACTED    → extraction complete (future)
    -- FAILED       → file could not be saved or processed
    status          TEXT        NOT NULL DEFAULT 'INGESTED'
                    CHECK (status IN ('INGESTED','EXTRACTING','EXTRACTED','FAILED')),

    -- Human-readable error message when status = FAILED.
    error_message   TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast look-ups of all documents belonging to a job.
CREATE INDEX IF NOT EXISTS idx_documents_job_id ON documents(job_id);

-- =============================================================================
-- TABLE: extraction_results
-- One row per (document × method × field) combination.
-- Canonical contract: ExtractionResult(document_id, method, field, value)
-- The UNIQUE constraint enforces idempotent upserts — re-running extraction
-- for the same (document, method, field) updates rather than duplicates.
-- =============================================================================
CREATE TABLE IF NOT EXISTS extraction_results (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Which document this extraction belongs to.
    document_id         UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    -- Which extraction method produced this result.
    -- V1 methods: textract | ocr | gpt | claude
    method              TEXT        NOT NULL
                        CHECK (method IN ('textract','ocr','gpt','claude','nvidia')),

    -- Which V1 target field was extracted.
    field               TEXT        NOT NULL
                        CHECK (field IN ('person_name','company_name','contract_date',
                                         'contract_value','address')),

    -- The raw extracted text value.  NULL means the extractor found nothing.
    value               TEXT,

    -- ── Optional debug / audit fields ─────────────────────────────────────────
    -- The snippet of source text the extractor used as evidence.
    evidence_snippet    TEXT,

    -- Extractor's own internal confidence (0.0–1.0), if it provides one.
    -- Different from our deterministic confidence_score in consolidated_fields.
    method_confidence   FLOAT       CHECK (method_confidence IS NULL OR
                                           (method_confidence >= 0 AND method_confidence <= 1)),

    -- Short machine-readable error code if extraction failed for this field.
    error_code          TEXT,

    -- Human-readable error message if extraction failed for this field.
    error_message       TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- THE KEY CONSTRAINT: prevents double-writing the same (doc, method, field).
    -- Upsert ON CONFLICT (document_id, method, field) DO UPDATE is safe against this.
    UNIQUE (document_id, method, field)
);

CREATE INDEX IF NOT EXISTS idx_extraction_results_document_id
    ON extraction_results(document_id);

-- =============================================================================
-- TABLE: consolidated_fields
-- One row per (document × field) after running consolidation + confidence steps.
-- Canonical contract: ConsolidatedField(document_id, field, final_value, confidence_score, status)
-- =============================================================================
CREATE TABLE IF NOT EXISTS consolidated_fields (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Which document this consolidated result belongs to.
    document_id         UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    -- Which V1 target field was consolidated.
    field               TEXT        NOT NULL
                        CHECK (field IN ('person_name','company_name','contract_date',
                                         'contract_value','address')),

    -- The agreed-upon final value after running consolidation logic.
    -- NULL if no extractor produced a value.
    final_value         TEXT,

    -- Deterministic confidence score (0–100).
    -- Factors: agreement across methods, format validity, presence/absence.
    -- Color buckets: Green(95+), Yellow(80–94), Red(<80).
    confidence_score    INT         CHECK (confidence_score IS NULL OR
                                           (confidence_score >= 0 AND confidence_score <= 100)),

    -- Human-readable status label: e.g., 'GREEN', 'YELLOW', 'RED', 'MISSING'.
    status              TEXT,

    -- JSONB breakdown for explainability — stores which methods agreed, which
    -- rule was applied, and the per-factor confidence decomposition.
    -- Example: {"rule_used": "agreement", "matched_methods": ["gpt","claude"], ...}
    breakdown           JSONB,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- THE KEY CONSTRAINT: one consolidated result per (document, field).
    -- Upsert ON CONFLICT (document_id, field) DO UPDATE keeps this idempotent.
    UNIQUE (document_id, field)
);

CREATE INDEX IF NOT EXISTS idx_consolidated_fields_document_id
    ON consolidated_fields(document_id);
