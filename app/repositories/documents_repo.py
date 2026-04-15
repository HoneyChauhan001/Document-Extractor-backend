"""
app/repositories/documents_repo.py
────────────────────────────────────
Repository for the `documents` table.

RESPONSIBILITY:
• All direct ORM reads/writes to the `documents` table live here.
• Keeps DB logic out of services and route handlers.
"""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.documents import Document


def create_document(
    db: Session,
    job_id: uuid.UUID,
    filename: str,
    file_path: Optional[str],
    file_type: Optional[str],
    status: str = "INGESTED",
    error_message: Optional[str] = None,
) -> Document:
    """
    Insert a new Document row.

    Called once per uploaded file during ingestion.

    Args:
        db:            Active SQLAlchemy session.
        job_id:        UUID of the parent job.
        filename:      Sanitised filename string (no path components).
        file_path:     Absolute path where file was saved, or None if save failed.
        file_type:     'pdf' | 'docx' | None (None only when status == 'FAILED').
        status:        'INGESTED' (success) or 'FAILED' (could not save file).
        error_message: Human-readable reason for failure; None on success.

    Returns:
        The new Document ORM object (flushed but not committed).
    """
    doc = Document(
        id=uuid.uuid4(),
        job_id=job_id,
        filename=filename,
        file_path=file_path,
        file_type=file_type,
        status=status,
        error_message=error_message,
    )
    db.add(doc)
    db.flush()  # Send INSERT within current transaction without committing.
    return doc


def get_documents_for_job(db: Session, job_id: uuid.UUID) -> list[Document]:
    """
    Return all Document rows for a given job, ordered by created_at ascending.

    Used by the extraction orchestrator (Step 2) to discover which documents
    need processing.
    """
    return (
        db.query(Document)
        .filter(Document.job_id == job_id)
        .order_by(Document.created_at.asc())
        .all()
    )
