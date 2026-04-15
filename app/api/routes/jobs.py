"""
app/api/routes/jobs.py
───────────────────────
POST /jobs — file upload + ingestion route.

ROUTE RESPONSIBILITY (thin layer):
1. Validate file count (1–5).
2. Validate file types (pdf/docx by extension).
3. Delegate all business logic to ingestion_service.ingest_job().
4. Return the service's response with HTTP 201.

WHY validations here instead of in the service?
• HTTP-level constraints (count, type) belong at the boundary.
• The service should not need to know about HTTP semantics.
• Returning 400 before even entering the service is cheaper and cleaner.
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db.session import get_db
import app.services.ingestion as ingestion_service
from app.schemas.jobs import JobCreateResponse

router = APIRouter()

# V1 permitted file extensions (lower-case, with leading dot).
_ALLOWED_EXTENSIONS = {".pdf", ".docx"}

# MVP constraint: 1 to 5 documents per job.
_MIN_FILES = 1
_MAX_FILES = 5


def _get_extension(filename: str | None) -> str:
    """Return the lower-case file extension including the dot, or '' if absent."""
    if not filename:
        return ""
    # rsplit on '.' to handle filenames with multiple dots (e.g., my.contract.pdf).
    parts = filename.rsplit(".", 1)
    return f".{parts[-1].lower()}" if len(parts) == 2 else ""


@router.post(
    "/jobs",
    response_model=JobCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload 1–5 contract documents to start a new extraction job",
    tags=["jobs"],
)
def create_job(
    files: list[UploadFile] = File(..., description="PDF or DOCX contract files (1–5)"),
    db: Session = Depends(get_db),
) -> JobCreateResponse:
    """
    Accept 1–5 PDF/DOCX contract files, persist them to disk, and create
    a job + document records in Postgres.

    Validation (HTTP 400 on failure):
    • File count must be between 1 and 5.
    • Every file must have a .pdf or .docx extension.

    On success (HTTP 201):
    • Returns job_id, per-document statuses, and any per-file errors.
    • Even if one file fails (PARTIAL_FAILURE), the response is still 201
      so the caller knows the job was created and can inspect per-file errors.
    """
    # ── Guard: file count ──────────────────────────────────────────────────────
    if len(files) < _MIN_FILES or len(files) > _MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"You must upload between {_MIN_FILES} and {_MAX_FILES} files. "
                f"Received: {len(files)}."
            ),
        )

    # ── Guard: file types ─────────────────────────────────────────────────────
    # We validate ALL files before starting any I/O so the caller gets all
    # validation errors in one response instead of discovering them one-by-one.
    invalid_files = [
        f.filename
        for f in files
        if _get_extension(f.filename) not in _ALLOWED_EXTENSIONS
    ]
    if invalid_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Only PDF and DOCX files are accepted. "
                f"Invalid file(s): {', '.join(str(n) for n in invalid_files)}"
            ),
        )

    # ── Delegate to service ───────────────────────────────────────────────────
    # The service handles disk writes, DB inserts, fault tolerance, and status.
    return ingestion_service.ingest_job(files=files, db=db)
