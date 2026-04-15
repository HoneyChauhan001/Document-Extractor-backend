"""
app/api/routes/export.py
─────────────────────────
GET /documents/{document_id}/export — download consolidated fields as CSV.

ROUTE RESPONSIBILITY (thin):
1. Validate the document exists (404 if not).
2. Delegate to export_service.export_document_csv().
3. Stream the CSV back with Content-Disposition: attachment.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.models.documents import Document
from app.db.session import get_db
from app.services.export import export_document_csv

router = APIRouter()


@router.get(
    "/documents/{document_id}/export",
    summary="Export consolidated fields for a document as CSV",
    tags=["documents"],
    response_class=StreamingResponse,
)
def export_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Return the consolidated extraction results for a document as a CSV download.

    CSV columns (exact, in order): field_name, final_value, confidence_score
    Rows follow V1_FIELDS canonical order:
      person_name, company_name, contract_date, contract_value, address

    Returns HTTP 404 if the document_id does not exist.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found.",
        )

    csv_content, base_filename = export_document_csv(document_id=document_id, db=db)

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{base_filename}.csv"'
        },
    )
