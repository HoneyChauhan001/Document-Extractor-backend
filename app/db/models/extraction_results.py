"""
app/db/models/extraction_results.py
──────────────────────────────────────
ORM model for the `extraction_results` table.

Canonical contract (from copilot-instructions.md):
    ExtractionResult(document_id, method, field, value)

KEY DESIGN CHOICES:
- UNIQUE (document_id, method, field) allows idempotent upserts.
  Re-running the same extractor on the same document updates the row,
  not duplicates it.
- One row is written per (method, field) EVEN IF value is NULL.
  This is important: a NULL value means "extractor ran but found nothing",
  which is different from "extractor never ran" (no row at all).
  Consolidation logic depends on this distinction.
- Optional debug fields (evidence_snippet, method_confidence, error_code,
  error_message) do not change the canonical contract's meaning.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base

# The ordered list of V1 target fields.  Order matters: consolidation logic
# iterates fields in this order to produce deterministic output.
V1_FIELDS = ("person_name", "company_name", "contract_date", "contract_value", "address")

# The ordered list of extraction methods.  Order matters: tie-breaking in
# consolidation uses this priority when no agreement is reached.
EXTRACTION_METHODS = ("textract", "ocr", "gpt", "claude", "nvidia")


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Extraction method name.  Must be one of EXTRACTION_METHODS.
    method: Mapped[str] = mapped_column(String(50), nullable=False)

    # V1 target field name.  Must be one of V1_FIELDS.
    field: Mapped[str] = mapped_column(String(50), nullable=False)

    # Raw extracted text.  NULL = extractor found no value for this field.
    value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Optional debug fields ──────────────────────────────────────────────────

    # The source-text snippet the extractor used as evidence for its answer.
    # Useful for human review (Step 7 UI) and debugging wrong extractions.
    evidence_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The extractor's own internal confidence score (0.0–1.0), if it provides one.
    # Note: this is the METHOD's self-reported score, not our deterministic score.
    method_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Short machine-readable error code when extraction failed for this field.
    # Examples: "TIMEOUT", "API_ERROR", "PARSE_FAILURE"
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Human-readable error detail for debugging.
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

    # Relationship for ORM navigation; does not add a DB column.
    document: Mapped["Document"] = relationship(  # noqa: F821
        "Document",
        back_populates="extraction_results",
    )

    __table_args__ = (
        # THE CORE CONSTRAINT — idempotent upsert depends on this.
        UniqueConstraint("document_id", "method", "field", name="uq_extraction_result"),
        CheckConstraint(
            f"method IN {EXTRACTION_METHODS}",
            name="ck_extraction_method",
        ),
        CheckConstraint(
            f"field IN {V1_FIELDS}",
            name="ck_extraction_field",
        ),
        CheckConstraint(
            "method_confidence IS NULL OR (method_confidence >= 0 AND method_confidence <= 1)",
            name="ck_extraction_method_confidence",
        ),
    )
