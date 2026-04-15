"""
app/services/confidence.py
───────────────────────────
Step 5: Deterministic confidence scoring (0–100) per consolidated field.

SCORING ALGORITHM:
  +20   value is non-null (presence)
  +50   ≥2 methods agree on normalized value
  +10   ≥3 methods agree (stacks with above → +60 total for agreement)
  +20   value passes field-specific format validation
  ──────────────────────────────────────────────────────────────────────
  100   max possible

COLOR STATUS BUCKETS:
  GREEN   → score ≥ 95
  YELLOW  → score 80–94
  RED     → score 1–79
  MISSING → score = 0 (no value)

After scoring, confidence_factors is merged into the existing breakdown JSONB
so the full audit trail is preserved in one column.
"""

import re
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.extractors.base import EXTRACTION_METHODS, V1_FIELDS
from app.repositories.consolidated_fields_repo import (
    get_consolidated_fields_for_document,
    upsert_consolidated_fields,
)
from app.services.consolidation import normalize_for_comparison

logger = get_logger(__name__)


def _validate_field(field: str, value: Optional[str]) -> bool:
    """
    Return True if `value` passes field-specific format validation.

    contract_date   → parseable as any common date format
    contract_value  → contains at least one digit
    person_name,
    company_name,
    address         → non-empty after strip
    """
    if not value:
        return False

    if field == "contract_date":
        from datetime import datetime

        formats = [
            "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
            "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
            "%Y/%m/%d", "%m-%d-%Y",
        ]
        for fmt in formats:
            try:
                datetime.strptime(value, fmt)
                return True
            except ValueError:
                continue
        try:
            from dateutil import parser as _dateutil_parser  # type: ignore[import]
            _dateutil_parser.parse(value)
            return True
        except Exception:
            return False

    elif field == "contract_value":
        return bool(re.search(r"\d", value))

    else:
        # person_name, company_name, address
        return bool(value.strip())


def _color_status(score: int, final_value: Optional[str]) -> str:
    if final_value is None or score == 0:
        return "MISSING"
    if score >= 95:
        return "GREEN"
    if score >= 80:
        return "YELLOW"
    return "RED"


def score_document(document_id: uuid.UUID, db: Session) -> None:
    """
    Compute and persist confidence scores for all consolidated fields of a document.

    Reads the existing consolidated_fields rows (including the candidates stored
    in breakdown by the consolidation step) and re-normalizes values to count
    cross-method agreement. Updates confidence_score, status, and merges a
    confidence_factors dict into the existing breakdown JSONB.

    Args:
        document_id: UUID of the document to score.
        db:          Active SQLAlchemy session (caller commits after this returns).
    """
    consolidated_rows = get_consolidated_fields_for_document(
        db=db, document_id=document_id
    )

    if not consolidated_rows:
        logger.warning(
            f"No consolidated_fields rows found for document_id={document_id} — "
            "run consolidation first."
        )
        return

    updates = []

    for row in consolidated_rows:
        field = row.field
        final_value = row.final_value
        existing_breakdown: dict = dict(row.breakdown) if row.breakdown else {}

        # Candidates were stored during consolidation.
        candidates: dict[str, Optional[str]] = existing_breakdown.get("candidates", {})

        score = 0
        score_factors: dict[str, int] = {}

        if final_value is not None:
            # ── Presence ─────────────────────────────────────────────────────
            score += 20
            score_factors["presence"] = 20

            # ── Agreement ────────────────────────────────────────────────────
            norm_final = normalize_for_comparison(field, final_value)
            agree_count = sum(
                1
                for m in EXTRACTION_METHODS
                if candidates.get(m) is not None
                and normalize_for_comparison(field, candidates[m]) == norm_final
            )

            agree_points = 0
            if agree_count >= 3:
                agree_points = 60
            elif agree_count >= 2:
                agree_points = 50
            score += agree_points
            score_factors["agreement"] = agree_points

            # ── Format validity ───────────────────────────────────────────────
            validity_points = 20 if _validate_field(field, final_value) else 0
            score += validity_points
            score_factors["validity"] = validity_points
        else:
            score_factors["presence"] = 0
            score_factors["agreement"] = 0
            score_factors["validity"] = 0

        status = _color_status(score, final_value)
        merged_breakdown = {**existing_breakdown, "confidence_factors": score_factors}

        updates.append({
            "field": field,
            "final_value": final_value,
            "confidence_score": score,
            "status": status,
            "breakdown": merged_breakdown,
        })

        logger.info(
            f"Scored document_id={document_id} field={field} "
            f"score={score} status={status} factors={score_factors}"
        )

    upsert_consolidated_fields(db=db, document_id=document_id, results=updates)
