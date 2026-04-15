"""
app/services/consolidation.py
──────────────────────────────
Step 4: Deterministic agreement-first consolidation.

ALGORITHM:
1. Read all extraction_results rows for the document.
2. Normalize each value for comparison (strip/casefold/field-specific rules).
3. Select final_value per V1 field using agreement-first rules:
   Rule 1 — ≥2 methods agree on normalized form → use first method's original casing.
   Rule 2 — Fallback: prefer Textract if non-null.
   Rule 3 — Fallback: first non-null in priority order: gpt → claude → ocr → textract.
   Rule 4 — All null → final_value = None.
4. Build explainability metadata (stored as JSONB breakdown).
5. Upsert into consolidated_fields.
"""

import re
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.extractors.base import EXTRACTION_METHODS, V1_FIELDS
from app.repositories.consolidated_fields_repo import upsert_consolidated_fields
from app.repositories.extraction_results_repo import get_results_for_document

logger = get_logger(__name__)

# Priority order for Rule 3 (first non-null fallback).
_PRIORITY_ORDER = ("gpt", "claude", "nvidia", "ocr", "textract")


def _normalize_date(value: str) -> str:
    """Attempt to normalize a date string to YYYY-MM-DD. Returns original if unparseable."""
    from datetime import datetime

    formats = [
        "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
        "%Y/%m/%d", "%m-%d-%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    try:
        from dateutil import parser as _dateutil_parser  # type: ignore[import]
        return _dateutil_parser.parse(value, dayfirst=False).strftime("%Y-%m-%d")
    except Exception:
        pass

    return value


def normalize_for_comparison(field: str, value: Optional[str]) -> Optional[str]:
    """
    Normalize a raw extracted value for cross-method agreement comparison.

    - Strips whitespace, collapses internal whitespace.
    - Casefolds (for comparison only — originals are kept for storage).
    - Field-specific:
        contract_date   → YYYY-MM-DD if parseable
        contract_value  → strip currency symbols and commas

    Returns None if value is None or empty after normalization.
    """
    if value is None:
        return None

    normalized = re.sub(r"\s+", " ", value.strip())
    if not normalized:
        return None

    normalized = normalized.casefold()

    if field == "contract_date":
        normalized = _normalize_date(normalized)
    elif field == "contract_value":
        normalized = re.sub(r"[$£€,]", "", normalized)

    return normalized


def consolidate_document(document_id: uuid.UUID, db: Session) -> list[dict]:
    """
    Run agreement-first consolidation for all V1 fields of a document.

    Reads extraction_results, applies the selection algorithm per field,
    builds explainability metadata, and upserts into consolidated_fields.

    Args:
        document_id: UUID of the document to consolidate.
        db:          Active SQLAlchemy session (caller commits after this returns).

    Returns:
        List of dicts with keys: field, final_value, breakdown, winning_method.
    """
    rows = get_results_for_document(db=db, document_id=document_id)

    # Build: {field: {method: (original_value, normalized_value)}}
    field_method_vals: dict[str, dict[str, tuple[Optional[str], Optional[str]]]] = {
        f: {} for f in V1_FIELDS
    }
    for row in rows:
        if row.field in field_method_vals:
            field_method_vals[row.field][row.method] = (
                row.value,
                normalize_for_comparison(row.field, row.value),
            )

    consolidation_results = []

    for field in V1_FIELDS:
        method_vals = field_method_vals[field]

        # Candidates dict: {method: original_value} — all 4 methods, None if absent.
        candidates: dict[str, Optional[str]] = {
            m: method_vals.get(m, (None, None))[0] for m in EXTRACTION_METHODS
        }
        norm_candidates: dict[str, Optional[str]] = {
            m: method_vals.get(m, (None, None))[1] for m in EXTRACTION_METHODS
        }

        # ── Rule 1: Agreement — ≥2 methods agree on normalized value ─────────
        agreement_groups: dict[str, list[str]] = {}
        for method, norm_val in norm_candidates.items():
            if norm_val is not None:
                agreement_groups.setdefault(norm_val, []).append(method)

        final_value: Optional[str] = None
        winning_method: Optional[str] = None
        rule_used: str = "all_null"
        matched_methods: list[str] = []

        for _norm_val, methods_list in agreement_groups.items():
            if len(methods_list) >= 2:
                rule_used = "agreement"
                matched_methods = methods_list
                # Use first method in canonical EXTRACTION_METHODS order for casing.
                winning_method = next(
                    m for m in EXTRACTION_METHODS if m in methods_list
                )
                final_value = method_vals[winning_method][0]
                break

        if rule_used == "all_null":
            # ── Rule 2: Prefer Textract if non-null ──────────────────────────
            textract_orig = method_vals.get("textract", (None, None))[0]
            if textract_orig is not None:
                rule_used = "textract_preference"
                winning_method = "textract"
                final_value = textract_orig
            else:
                # ── Rule 3: First non-null in priority order ──────────────────
                for m in _PRIORITY_ORDER:
                    orig = method_vals.get(m, (None, None))[0]
                    if orig is not None:
                        rule_used = "priority_fallback"
                        winning_method = m
                        final_value = orig
                        break

        breakdown = {
            "rule_used": rule_used,
            "matched_methods": matched_methods,
            "winning_method": winning_method,
            "candidates": candidates,
        }

        consolidation_results.append({
            "field": field,
            "final_value": final_value,
            "confidence_score": None,
            "status": None,
            "breakdown": breakdown,
        })

        logger.info(
            f"Consolidated document_id={document_id} field={field} "
            f"rule={rule_used} winning_method={winning_method} value={repr(final_value)}"
        )

    upsert_consolidated_fields(db=db, document_id=document_id, results=consolidation_results)

    return [
        {
            "field": r["field"],
            "final_value": r["final_value"],
            "breakdown": r["breakdown"],
            "winning_method": r["breakdown"]["winning_method"],
        }
        for r in consolidation_results
    ]
