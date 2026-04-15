"""
app/db/models/documents.py
───────────────────────────
ORM model for the `documents` table.

A Document represents one uploaded file within a Job.
- `file_path` is the absolute path on disk where bytes were saved.
- `file_type` is 'pdf' or 'docx' (determined by extension at ingestion, not MIME).
- `status` tracks this document's pipeline progress independently of other documents.
  (Fault-tolerance rule: one document failing must not block others.)
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign key to the parent job.  Indexed for fast lookups of a job's documents.
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Original filename as submitted (sanitised at service layer before saving).
    filename: Mapped[str] = mapped_column(String, nullable=False)

    # Absolute path on disk.  NULL if the file failed to write.
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extension-based type detection.  'pdf' or 'docx'.
    file_type: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Per-document lifecycle status.
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="INGESTED",
    )

    # Populated when status = 'FAILED' to explain what went wrong.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships (in-memory navigation only — no extra SQL columns).
    job: Mapped["Job"] = relationship("Job", back_populates="documents")  # noqa: F821

    extraction_results: Mapped[list["ExtractionResult"]] = relationship(  # noqa: F821
        "ExtractionResult",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    consolidated_fields: Mapped[list["ConsolidatedField"]] = relationship(  # noqa: F821
        "ConsolidatedField",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('INGESTED','EXTRACTING','EXTRACTED','FAILED')",
            name="ck_documents_status",
        ),
        CheckConstraint(
            "file_type IN ('pdf','docx')",
            name="ck_documents_file_type",
        ),
    )
