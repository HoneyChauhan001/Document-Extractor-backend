"""
app/api/routes/review.py
─────────────────────────
GET /documents/{document_id}/results — review extraction + consolidation results.

ROUTE RESPONSIBILITY (thin):
1. Validate the document exists (404 if not).
2. Read extraction_results (per-method candidates) and consolidated_fields.
3. Return structured JSON with final_value, confidence_score, status, candidates,
   and breakdown per V1 field.

Designed for UI review: surfaces all method-level candidates alongside the final
consolidated decision so a human reviewer can validate or override results.
"""

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models.documents import Document
from app.db.session import get_db
from app.extractors.base import EXTRACTION_METHODS, V1_FIELDS
from app.repositories.consolidated_fields_repo import get_consolidated_fields_for_document
from app.repositories.extraction_results_repo import get_results_for_document

router = APIRouter()


class FieldResult(BaseModel):
    """Per-field review data: consolidated result + all method-level candidates."""

    final_value: Optional[str]
    confidence_score: Optional[int]
    status: Optional[str]
    candidates: dict[str, Optional[str]]
    breakdown: Optional[dict[str, Any]]


class DocumentReviewResponse(BaseModel):
    """Response body for GET /documents/{document_id}/results."""

    document_id: uuid.UUID
    fields: dict[str, FieldResult]


@router.get(
    "/documents/{document_id}/results",
    response_model=DocumentReviewResponse,
    summary="Review extraction and consolidation results for a document",
    tags=["documents"],
)
def get_document_results(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> DocumentReviewResponse:
    """
    Return extraction candidates and consolidated results for all V1 fields.

    For each of the 5 V1 fields the response includes:
    - final_value: the agreed-upon value from consolidation
    - confidence_score: 0–100 deterministic score
    - status: GREEN / YELLOW / RED / MISSING
    - candidates: per-method raw values from extraction
    - breakdown: explainability metadata (rule used, matched methods, etc.)

    Returns HTTP 404 if the document_id does not exist.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found.",
        )

    extraction_rows = get_results_for_document(db=db, document_id=document_id)
    consolidated_rows = get_consolidated_fields_for_document(db=db, document_id=document_id)

    # Build: {field: {method: value}} — None for methods with no row yet.
    extraction_by_field: dict[str, dict[str, Optional[str]]] = {
        f: {m: None for m in EXTRACTION_METHODS} for f in V1_FIELDS
    }
    for row in extraction_rows:
        if row.field in extraction_by_field:
            extraction_by_field[row.field][row.method] = row.value

    consolidated_by_field = {row.field: row for row in consolidated_rows}

    fields_result: dict[str, FieldResult] = {}
    for field in V1_FIELDS:
        cons = consolidated_by_field.get(field)
        fields_result[field] = FieldResult(
            final_value=cons.final_value if cons else None,
            confidence_score=cons.confidence_score if cons else None,
            status=cons.status if cons else None,
            candidates=extraction_by_field[field],
            breakdown=dict(cons.breakdown) if cons and cons.breakdown else None,
        )

    return DocumentReviewResponse(
        document_id=document_id,
        fields=fields_result,
    )
