"""
app/extractors/base.py
───────────────────────
Canonical extractor interface + ExtractionResult dataclass.

WHY a shared base?
• All four extractors (Textract, OCR, GPT, Claude) must return data in the
  exact same shape so the orchestrator and repo can handle them identically.
• A shared abstract base class enforces the contract at definition time —
  if a new extractor forgets to implement `extract()`, Python raises an error
  immediately on import, not at runtime.

CANONICAL CONTRACT (from copilot-instructions.md):
    ExtractionResult(document_id, method, field, value)
    + optional debug fields: evidence_snippet, method_confidence, error_code, error_message

KEY RULES:
- One ExtractionResult per (method × V1 field) — always, even if value is None.
  "None value" means "extractor ran but found nothing" — different from no row.
- Fields and methods are always iterated in the FIXED CANONICAL ORDER defined
  here, so consolidation output is deterministic regardless of extractor speed.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# ── Canonical V1 field list (ORDER MATTERS for deterministic consolidation) ───
# Do not reorder — consolidation and confidence scoring depend on this order.
V1_FIELDS: tuple[str, ...] = (
    "person_name",
    "company_name",
    "contract_date",
    "contract_value",
    "address",
)

# ── Canonical extraction method list (ORDER MATTERS for tie-breaking) ─────────
# In consolidation, if no agreement is reached:
#   1. Prefer Textract
#   2. Then first non-empty in this priority order: gpt → claude → nvidia → ocr
# This order is defined once here and used everywhere.
EXTRACTION_METHODS: tuple[str, ...] = ("textract", "ocr", "gpt", "claude", "nvidia")


@dataclass
class ExtractionResult:
    """
    In-memory representation of one extracted field value.

    This is the canonical data contract used between extractors, the
    orchestrator, and the persistence layer.  It matches the DB model
    in app/db/models/extraction_results.py field-for-field.

    Always created even when value is None (extractor ran, found nothing).
    error_code/error_message are populated when the extractor itself failed.
    """

    document_id: uuid.UUID         # Which document this was extracted from
    method: str                    # 'textract' | 'ocr' | 'gpt' | 'claude'
    field: str                     # One of V1_FIELDS
    value: Optional[str]           # Extracted text; None = not found

    # ── Optional debug / audit fields ─────────────────────────────────────────
    evidence_snippet: Optional[str] = field(default=None)
    # The passage of source text that the extractor used as evidence.

    method_confidence: Optional[float] = field(default=None)
    # Extractor's own internal confidence (0.0–1.0), if it reports one.
    # NOT the same as our deterministic confidence_score in consolidated_fields.

    error_code: Optional[str] = field(default=None)
    # Short machine-readable error code: 'TIMEOUT' | 'API_ERROR' | 'PARSE_FAILURE' | etc.

    error_message: Optional[str] = field(default=None)
    # Human-readable error detail for debugging.


class BaseExtractor(ABC):
    """
    Abstract base class every extractor must implement.

    Subclasses implement `extract()` and return one ExtractionResult per
    V1 field, in V1_FIELDS order, regardless of whether a value was found.

    The orchestrator calls `safe_extract()` (defined here) which wraps
    `extract()` in a try/except so one extractor failing never raises.
    """

    # Subclasses set this to their method name string:  'textract' | 'ocr' | etc.
    method: str = ""

    @abstractmethod
    def extract(
        self,
        document_id: uuid.UUID,
        file_path: str,
        file_type: str,
    ) -> list[ExtractionResult]:
        """
        Run extraction on a single document file.

        Args:
            document_id: UUID of the document row in the DB.
            file_path:   Absolute path to the file on disk.
            file_type:   'pdf' or 'docx'.

        Returns:
            List of ExtractionResult — exactly one per V1 field, in V1_FIELDS order.
            value=None if the field was not found; error fields set if extraction failed.

        MUST be implemented by every subclass.
        """
        ...

    def safe_extract(
        self,
        document_id: uuid.UUID,
        file_path: str,
        file_type: str,
    ) -> list[ExtractionResult]:
        """
        Fault-tolerant wrapper around extract().

        If extract() raises any exception, returns a full set of ExtractionResults
        with value=None and error metadata for every V1 field — so the orchestrator
        always gets the same shape of response and can persist error rows safely.

        This is the method the orchestrator calls; individual extractors implement extract().
        """
        try:
            return self.extract(document_id, file_path, file_type)
        except Exception as exc:
            # Return null results for all fields with the error captured.
            # The pipeline continues; this extractor's failure is recorded in DB.
            return [
                ExtractionResult(
                    document_id=document_id,
                    method=self.method,
                    field=f,
                    value=None,
                    error_code="EXTRACTOR_EXCEPTION",
                    error_message=str(exc),
                )
                for f in V1_FIELDS
            ]
