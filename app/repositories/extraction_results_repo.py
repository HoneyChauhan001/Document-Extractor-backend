"""
app/repositories/extraction_results_repo.py
─────────────────────────────────────────────
Repository for the `extraction_results` table.

KEY DESIGN: Upsert semantics (idempotent writes).
The UNIQUE constraint on (document_id, method, field) means re-running
the same extractor on the same document must UPDATE the existing row,
not insert a duplicate.  We use PostgreSQL's ON CONFLICT DO UPDATE for this.

WHY upsert instead of delete+insert?
• Preserves the row's `id` and `created_at` timestamp across re-runs.
• Atomic — no window between delete and insert where the row is missing.
• The ON CONFLICT target exactly matches the UNIQUE constraint in init.sql.

WHY write a row even when value is None?
• "None value" = extractor ran, found nothing.  This is meaningful for
  consolidation: it knows this method was attempted.
• Without the row, consolidation can't distinguish "not attempted" from
  "attempted and found nothing" — which matters for confidence scoring.
"""

import uuid
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.extractors.base import ExtractionResult


def upsert_extraction_results(
    db: Session,
    results: Sequence[ExtractionResult],
) -> None:
    """
    Upsert a batch of ExtractionResult objects into the extraction_results table.

    Uses a raw SQL INSERT … ON CONFLICT DO UPDATE so a single statement handles
    both new rows and updates to existing rows atomically.

    Args:
        db:      Active SQLAlchemy session (caller commits after this returns).
        results: One or more ExtractionResult objects — typically all results
                 for one (document_id, method) pair (5 rows = 5 V1 fields).

    The caller is responsible for calling db.commit() after all extractors
    have been persisted for a document.
    """
    if not results:
        return

    # Build parameter dicts — one per ExtractionResult.
    params = [
        {
            "id": uuid.uuid4(),
            "document_id": str(r.document_id),  # str for psycopg UUID bind
            "method": r.method,
            "field": r.field,
            "value": r.value,
            "evidence_snippet": r.evidence_snippet,
            "method_confidence": r.method_confidence,
            "error_code": r.error_code,
            "error_message": r.error_message,
        }
        for r in results
    ]

    # Raw SQL upsert — ON CONFLICT matches the UNIQUE constraint name from init.sql.
    # DO UPDATE SET ensures all mutable columns are refreshed on re-run.
    # NOTE: Use CAST() instead of ::uuid — psycopg3 conflicts with :param::type syntax
    #       because it tries to rewrite :param as $N but leaves ::type, breaking the SQL.
    upsert_sql = text("""
        INSERT INTO extraction_results
            (id, document_id, method, field, value,
             evidence_snippet, method_confidence, error_code, error_message,
             created_at, updated_at)
        VALUES
            (CAST(:id AS uuid), CAST(:document_id AS uuid), :method, :field, :value,
             :evidence_snippet, :method_confidence, :error_code, :error_message,
             NOW(), NOW())
        ON CONFLICT ON CONSTRAINT extraction_results_document_id_method_field_key
        DO UPDATE SET
            value              = EXCLUDED.value,
            evidence_snippet   = EXCLUDED.evidence_snippet,
            method_confidence  = EXCLUDED.method_confidence,
            error_code         = EXCLUDED.error_code,
            error_message      = EXCLUDED.error_message,
            updated_at         = NOW()
    """)

    # Execute all rows in a single DB round-trip using executemany semantics.
    db.execute(upsert_sql, params)


def get_results_for_document(
    db: Session,
    document_id: uuid.UUID,
) -> list:
    """
    Return all extraction_results rows for a document.

    Used by consolidation (Step 4) to read all method-wise values
    before running the agreement-first algorithm.

    Returns raw Row objects ordered by (method, field) for deterministic processing.
    """
    rows = db.execute(
        text("""
            SELECT document_id, method, field, value,
                   evidence_snippet, method_confidence, error_code, error_message
            FROM extraction_results
            WHERE document_id = CAST(:doc_id AS uuid)
            ORDER BY method, field
        """),
        {"doc_id": str(document_id)},
    ).fetchall()
    return rows
