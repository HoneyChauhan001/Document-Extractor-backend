"""
app/db/models/consolidated_fields.py
──────────────────────────────────────
ORM model for the `consolidated_fields` table.

Canonical contract (from copilot-instructions.md):
    ConsolidatedField(document_id, field, final_value, confidence_score, status)

KEY DESIGN CHOICES:
- UNIQUE (document_id, field) enforces one agreed-upon result per field per document.
  Re-running consolidation upserts (updates) the existing row.
- `breakdown` JSONB stores the explainability metadata so the UI (Step 7) and
  human reviewers can understand WHY a particular value was chosen and how
  the confidence score was calculated.  Example breakdown:
    {
      "rule_used": "agreement",
      "matched_methods": ["gpt", "claude"],
      "candidates": {"textract": "Acme Corp", "gpt": "Acme Corp", "claude": "Acme Corp"},
      "confidence_factors": {"agreement": 40, "validity": 30, "presence": 25}
    }
- `status` is the color-bucket string: 'GREEN' (95+), 'YELLOW' (80–94), 'RED' (<80),
  or 'MISSING' if no extractor found any value.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base


class ConsolidatedField(Base):
    __tablename__ = "consolidated_fields"

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

    # One of V1_FIELDS: person_name | company_name | contract_date | contract_value | address
    field: Mapped[str] = mapped_column(String(50), nullable=False)

    # The agreed-upon final value after deterministic consolidation.
    # NULL if no extractor produced any value for this field on this document.
    final_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Deterministic confidence score 0–100.
    # Computed by the confidence engine (Step 5); NULL until that step runs.
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Color-bucket status string: 'GREEN' | 'YELLOW' | 'RED' | 'MISSING'
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # JSONB explainability payload serialised by the consolidation + confidence services.
    # Stored as native JSONB in Postgres for efficient querying/filtering in future UI.
    breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

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

    # Relationship for ORM navigation.
    document: Mapped["Document"] = relationship(  # noqa: F821
        "Document",
        back_populates="consolidated_fields",
    )

    __table_args__ = (
        # THE CORE CONSTRAINT — one final result per (document, field).
        UniqueConstraint("document_id", "field", name="uq_consolidated_field"),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 100)",
            name="ck_consolidated_confidence_score",
        ),
    )
