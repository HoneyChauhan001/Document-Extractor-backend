"""
app/services/export.py
───────────────────────
Step 6: CSV export for a single document.

CONTRACT (from copilot-instructions.md):
    Filename: <original_document_filename_without_extension>.csv
    Columns (exact order): field_name, final_value, confidence_score
    Row order follows V1_FIELDS canonical order.
    No extra columns.
"""

import csv
import io
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.documents import Document
from app.extractors.base import V1_FIELDS
from app.repositories.consolidated_fields_repo import get_consolidated_fields_for_document

logger = get_logger(__name__)


def export_document_csv(document_id: uuid.UUID, db: Session) -> tuple[str, str]:
    """
    Build a CSV string for the consolidated fields of a document.

    Args:
        document_id: UUID of the document to export.
        db:          Active SQLAlchemy session.

    Returns:
        (csv_content, base_filename) where base_filename has NO extension.
        The caller should append ".csv" when setting Content-Disposition.

    Raises:
        ValueError: if the document does not exist.
    """
    doc: Optional[Document] = (
        db.query(Document).filter(Document.id == document_id).first()
    )
    if doc is None:
        raise ValueError(f"Document {document_id} not found.")

    rows = get_consolidated_fields_for_document(db=db, document_id=document_id)
    field_map = {row.field: row for row in rows}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["field_name", "final_value", "confidence_score"])

    for field in V1_FIELDS:
        row = field_map.get(field)
        writer.writerow([
            field,
            row.final_value if row else None,
            row.confidence_score if row else None,
        ])

    # Strip extension from original filename so download is "contract.csv" not "contract.pdf.csv".
    base_filename = doc.filename
    if "." in base_filename:
        base_filename = base_filename.rsplit(".", 1)[0]

    logger.info(f"Built CSV export document_id={document_id} filename={base_filename}.csv")
    return output.getvalue(), base_filename
