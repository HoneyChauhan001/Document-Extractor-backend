"""
app/repositories/consolidated_fields_repo.py
──────────────────────────────────────────────
Repository for the `consolidated_fields` table.

KEY DESIGN: Upsert semantics (idempotent writes).
The UNIQUE constraint on (document_id, field) means re-running consolidation
must UPDATE the existing row, not insert a duplicate.

NOTE: Use CAST(:param AS uuid) / CAST(:param AS jsonb) — NOT ::type syntax.
      psycopg3 conflicts with :param::type because it rewrites :param to $N
      but leaves ::type, producing invalid SQL.
"""

import json
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def upsert_consolidated_fields(
    db: Session,
    document_id: uuid.UUID,
    results: list[dict],
) -> None:
    """
    Upsert consolidated field results for a document.

    Each dict in `results` must contain:
        field            (str)      — one of V1_FIELDS
        final_value      (str|None)
        confidence_score (int|None)
        status           (str|None)
        breakdown        (dict|None) — stored as JSONB

    Args:
        db:          Active SQLAlchemy session (caller commits).
        document_id: UUID of the document being consolidated.
        results:     List of per-field result dicts.
    """
    if not results:
        return

    params = [
        {
            "id": str(uuid.uuid4()),
            "document_id": str(document_id),
            "field": r["field"],
            "final_value": r.get("final_value"),
            "confidence_score": r.get("confidence_score"),
            "status": r.get("status"),
            "breakdown": json.dumps(r["breakdown"]) if r.get("breakdown") is not None else None,
        }
        for r in results
    ]

    upsert_sql = text("""
        INSERT INTO consolidated_fields
            (id, document_id, field, final_value, confidence_score, status, breakdown,
             created_at, updated_at)
        VALUES
            (CAST(:id AS uuid), CAST(:document_id AS uuid), :field, :final_value,
             :confidence_score, :status, CAST(:breakdown AS jsonb),
             NOW(), NOW())
        ON CONFLICT ON CONSTRAINT consolidated_fields_document_id_field_key
        DO UPDATE SET
            final_value      = EXCLUDED.final_value,
            confidence_score = EXCLUDED.confidence_score,
            status           = EXCLUDED.status,
            breakdown        = EXCLUDED.breakdown,
            updated_at       = NOW()
    """)

    db.execute(upsert_sql, params)


def get_consolidated_fields_for_document(
    db: Session,
    document_id: uuid.UUID,
) -> list:
    """
    Return all consolidated_fields rows for a document.

    Used by confidence scoring (Step 5), CSV export (Step 6), and
    the review API (Step 7).

    Returns raw Row objects ordered by field name for deterministic processing.
    """
    rows = db.execute(
        text("""
            SELECT document_id, field, final_value, confidence_score, status, breakdown
            FROM consolidated_fields
            WHERE document_id = CAST(:doc_id AS uuid)
            ORDER BY field
        """),
        {"doc_id": str(document_id)},
    ).fetchall()
    return rows
