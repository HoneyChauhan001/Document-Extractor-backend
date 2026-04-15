"""
app/api/routes/extract.py
──────────────────────────
POST /jobs/{job_id}/extract — trigger extraction for an existing job.

ROUTE RESPONSIBILITY (thin):
1. Validate the job_id exists.
2. Validate the job has documents in INGESTED status (not re-running on empty).
3. Delegate to orchestrator_service.run_extraction_for_job().
4. Return a summary response.

WHY a separate endpoint (not automatic after /jobs)?
Per the strict build order, extraction is a distinct, re-runnable pipeline stage.
Keeping it separate means:
- Ingestion and extraction can be retried independently.
- Future: extraction can be queued asynchronously (e.g., Celery task).
- Tests can verify ingestion without needing real extractors.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models.jobs import Job
from app.db.session import get_db
from app.services.orchestrator import run_extraction_for_job

router = APIRouter()


class ExtractionResponse(BaseModel):
    """Response body for POST /jobs/{job_id}/extract."""
    job_id: uuid.UUID
    total_documents: int
    succeeded: int
    failed: int
    extractor_errors: list[str]
    message: str


@router.post(
    "/jobs/{job_id}/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_200_OK,
    summary="Run all extractors on every document in a job",
    tags=["jobs"],
)
def trigger_extraction(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ExtractionResponse:
    """
    Trigger the extraction pipeline for an existing job.

    - Runs Textract, OCR, GPT, Claude on each document independently.
    - Persists one extraction_results row per (document, method, field).
    - One extractor or document failing does NOT abort others.
    - Returns a summary of how many documents were processed and any errors.

    Typical flow:
        POST /jobs           → creates job + documents (INGESTED)
        POST /jobs/{id}/extract → runs extractors, persists results
    """
    # ── Validate job exists ───────────────────────────────────────────────────
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )

    # ── Validate job is in an extractable state ───────────────────────────────
    # Prevent re-triggering on a job that has no documents or already failed fatally.
    if job.status == "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is still in PENDING status — ingestion may not have completed.",
        )

    # ── Delegate to orchestrator ──────────────────────────────────────────────
    summary = run_extraction_for_job(job_id=job_id, db=db)

    return ExtractionResponse(
        job_id=summary.job_id,
        total_documents=summary.total_documents,
        succeeded=summary.succeeded,
        failed=summary.failed,
        extractor_errors=summary.extractor_errors,
        message=(
            f"Extraction complete. "
            f"{summary.succeeded}/{summary.total_documents} document(s) processed."
        ),
    )
