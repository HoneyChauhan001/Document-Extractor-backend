"""
app/services/orchestrator.py
─────────────────────────────
Step 2 + Step 3: Extraction orchestration + persistence.
Step 4 + Step 5: Consolidation + confidence scoring (called after extraction per doc).

RESPONSIBILITY:
For each document in a job:
  1. Run all extractors (Textract, OCR, GPT, Claude) independently.
  2. After each extractor completes (or fails), immediately upsert its results
     into extraction_results — one row per (document_id, method, field).
  3. Commit extraction results per document.
  4. Run consolidation (agreement-first) and persist to consolidated_fields.
  5. Run confidence scoring and update consolidated_fields with scores + status.
  6. One extractor/consolidation/scoring failing must NEVER block other documents.

FAULT TOLERANCE CHAIN:
  Job → documents are processed independently (outer loop).
  Document → each extractor is wrapped in BaseExtractor.safe_extract()
             which catches ALL exceptions and returns null results with
             error metadata (no raise).
  Consolidation + scoring each wrapped in try/except — failure is logged only.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.extractors.registry import EXTRACTORS
from app.repositories.documents_repo import get_documents_for_job
from app.repositories.extraction_results_repo import upsert_extraction_results
from app.services.consolidation import consolidate_document
from app.services.confidence import score_document

logger = get_logger(__name__)


@dataclass
class ExtractionSummary:
    """Returned by run_extraction_for_job — useful for the API response."""
    job_id: uuid.UUID
    total_documents: int
    succeeded: int          # documents where at least one extractor returned a value
    failed: int             # documents that could not be read at all
    extractor_errors: list[str]   # per-extractor error messages (non-fatal)


def run_extraction_for_job(job_id: uuid.UUID, db: Session) -> ExtractionSummary:
    """
    Run all extractors on every document in a job.

    Flow per document:
      1. Fetch document row (file_path, file_type).
      2. For each extractor in EXTRACTORS (deterministic order):
         a. Call extractor.safe_extract() — never raises.
         b. Immediately upsert its results (commits after all extractors for this doc).
      3. Update document status.

    Returns an ExtractionSummary for the API response.
    """
    documents = get_documents_for_job(db=db, job_id=job_id)
    if not documents:
        logger.warning(f"No documents found for job_id={job_id}")
        return ExtractionSummary(job_id=job_id, total_documents=0,
                                 succeeded=0, failed=0, extractor_errors=[])

    succeeded = 0
    failed = 0
    all_extractor_errors: list[str] = []

    # ── Process each document independently ──────────────────────────────────
    for doc in documents:
        doc_errors: list[str] = []

        if doc.status == "FAILED" or not doc.file_path:
            # Document was never saved to disk during ingestion — skip extraction.
            logger.warning(
                f"Skipping extraction for document_id={doc.id} job_id={job_id} "
                f"(status={doc.status}, file_path={doc.file_path})"
            )
            failed += 1
            continue

        logger.info(
            f"Starting extraction job_id={job_id} document_id={doc.id} "
            f"file={doc.filename} type={doc.file_type}"
        )

        # ── Run each extractor independently ─────────────────────────────────
        # safe_extract() in BaseExtractor catches exceptions and returns null
        # results with error_code set — this loop never raises.
        for extractor in EXTRACTORS:
            results = extractor.safe_extract(
                document_id=doc.id,
                file_path=doc.file_path,
                file_type=doc.file_type or "",
            )

            # Collect any errors from this extractor for reporting.
            for r in results:
                if r.error_code and r.error_code not in ("NOT_ENABLED",):
                    # NOT_ENABLED is expected (extractor disabled by config) — not an error.
                    msg = (
                        f"method={extractor.method} field={r.field} "
                        f"error_code={r.error_code}: {r.error_message}"
                    )
                    doc_errors.append(msg)
                    logger.warning(
                        f"Extraction error document_id={doc.id} {msg}"
                    )
                else:
                    logger.info(
                        f"Extracted document_id={doc.id} method={extractor.method} "
                        f"field={r.field} value={'<none>' if r.value is None else repr(r.value[:60])}"
                    )

            # Persist this extractor's results immediately after it completes.
            # This way, partial results are saved even if a later extractor crashes.
            upsert_extraction_results(db=db, results=results)

        # ── Commit all extraction results for this document ───────────────────
        db.commit()
        logger.info(
            f"Committed extraction results document_id={doc.id} "
            f"job_id={job_id} extractor_errors={len(doc_errors)}"
        )

        # ── Consolidate: agreement-first selection per field ──────────────────
        try:
            consolidate_document(doc.id, db)
            db.commit()
            logger.info(f"Consolidated document_id={doc.id} job_id={job_id}")
        except Exception as exc:
            db.rollback()
            logger.error(f"Consolidation failed document_id={doc.id} job_id={job_id}: {exc}")

        # ── Score: compute 0–100 confidence + color status per field ─────────
        try:
            score_document(doc.id, db)
            db.commit()
            logger.info(f"Scored document_id={doc.id} job_id={job_id}")
        except Exception as exc:
            db.rollback()
            logger.error(f"Scoring failed document_id={doc.id} job_id={job_id}: {exc}")

        all_extractor_errors.extend(doc_errors)
        succeeded += 1

    return ExtractionSummary(
        job_id=job_id,
        total_documents=len(documents),
        succeeded=succeeded,
        failed=failed,
        extractor_errors=all_extractor_errors,
    )

