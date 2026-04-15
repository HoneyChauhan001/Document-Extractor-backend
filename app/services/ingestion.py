"""
app/services/ingestion.py
──────────────────────────
Step 1: Ingestion service — the real implementation.

RESPONSIBILITY:
Accepts a list of validated UploadFile objects, saves each to disk, and
creates the corresponding `jobs` + `documents` rows in Postgres.

FAULT TOLERANCE (per pipeline rules):
• Each file is processed independently inside its own try/except block.
• One file failing does NOT abort the others.
• Failed files get a Document row with status='FAILED' and an error_message.
• The job's final status reflects whether all files succeeded ('INGESTED'),
  some failed ('PARTIAL_FAILURE'), or all failed ('FAILED').

SECURITY:
• Filenames are sanitised before being used as filesystem paths.
  - os.path.basename() strips any directory components (e.g., "../../etc/passwd").
  - We strip leading dots/slashes and enforce a max length of 255 characters.
  - File type is determined by extension (not MIME type), which is spoofable
    but validated independently by the route before reaching this service.
    MVP assumption: route-level extension validation is sufficient for V1.
"""

import os
import uuid
from pathlib import Path
from typing import Sequence

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.documents_repo import create_document
from app.repositories.jobs_repo import create_job, update_job_status
from app.schemas.documents import DocumentResult
from app.schemas.jobs import JobCreateResponse

logger = get_logger(__name__)

# Maximum filename length accepted (filesystem typically allows 255 bytes).
_MAX_FILENAME_LEN = 255

# Mapping from file extension to the canonical file_type string stored in DB.
_EXT_TO_TYPE: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
}


def _sanitise_filename(original: str) -> str:
    """
    Return a safe filename string, stripping directory traversal components.

    Examples:
        "../../etc/passwd"   → "passwd"
        "./contract.pdf"     → "contract.pdf"
        "  my file.pdf  "   → "my file.pdf"
        "a" * 300 + ".pdf"  → truncated to 255 chars

    We use os.path.basename to strip any directory path, then strip leading
    dots/slashes to neutralise remaining traversal attempts.
    """
    name = os.path.basename(original)      # strip directory path components
    name = name.lstrip("./\\")             # strip any remaining leading . / \
    name = name.strip()                    # remove surrounding whitespace
    if not name:
        # If the filename reduced to empty after sanitisation, use a safe default.
        name = "unnamed_file"
    # Enforce max filesystem length (truncate stem, keep extension intact).
    if len(name) > _MAX_FILENAME_LEN:
        stem, ext = os.path.splitext(name)
        name = stem[: _MAX_FILENAME_LEN - len(ext)] + ext
    return name


def _detect_file_type(filename: str) -> str | None:
    """
    Determine 'pdf' or 'docx' from the file extension.

    Returns None if the extension is not recognised (should not happen
    because the route validates extensions before calling this service,
    but defensive coding here prevents a bad DB write).
    """
    _, ext = os.path.splitext(filename.lower())
    return _EXT_TO_TYPE.get(ext)


def _write_file_to_disk(job_id: uuid.UUID, filename: str, data: bytes) -> str:
    """
    Write `data` bytes to disk at STORAGE_DIR/<job_id>/<filename>.

    Creates the job-specific subdirectory if it does not yet exist.
    Returns the absolute path of the written file.
    """
    job_dir = Path(settings.STORAGE_DIR) / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)          # safe if dir already exists
    file_path = job_dir / filename
    file_path.write_bytes(data)
    return str(file_path.resolve())                     # store absolute path in DB


def ingest_job(files: Sequence[UploadFile], db: Session) -> JobCreateResponse:
    """
    Main ingestion entry point called by POST /jobs route handler.

    Flow:
    1. Create a `jobs` row in PENDING status.
    2. For each UploadFile (independently):
       a. Sanitise filename.
       b. Read bytes from the upload.
       c. Write bytes to disk.
       d. Create a `documents` row (INGESTED or FAILED).
    3. Update job status based on aggregate document outcomes.
    4. Commit the transaction (all DB writes in one commit for atomicity).
    5. Return JobCreateResponse.

    Args:
        files: Sequence of FastAPI UploadFile objects (already validated by route).
        db:    SQLAlchemy session (injected by get_db dependency).

    Returns:
        JobCreateResponse with job_id, status, per-document results, and errors list.
    """
    # ── Step 1: Create the job row ───────────────────────────────────────────
    job = create_job(db=db, document_count=len(files))
    logger.info(f"Created job job_id={job.id} with {len(files)} file(s)")

    document_results: list[DocumentResult] = []
    errors: list[str] = []

    # ── Step 2: Process each file independently ──────────────────────────────
    for upload in files:
        original_filename = upload.filename or "unknown"
        safe_filename = _sanitise_filename(original_filename)
        file_type = _detect_file_type(safe_filename)

        try:
            # Read all bytes from the upload stream.
            # For V1 (1–5 small-to-medium contract PDFs) this is fine in memory.
            # Future: stream to disk for large files.
            data = upload.file.read()

            # Write bytes to disk under the job's subdirectory.
            file_path = _write_file_to_disk(job.id, safe_filename, data)

            # Create a documents row recording the saved file.
            doc = create_document(
                db=db,
                job_id=job.id,
                filename=safe_filename,
                file_path=file_path,
                file_type=file_type,
                status="INGESTED",
            )

            logger.info(
                f"Ingested document file={safe_filename} "
                f"document_id={doc.id} job_id={job.id}"
            )

            document_results.append(
                DocumentResult(
                    document_id=doc.id,
                    filename=safe_filename,
                    status="INGESTED",
                )
            )

        except Exception as exc:
            # One file failing must NOT block the others — log, record, and continue.
            error_msg = f"Failed to ingest '{safe_filename}': {exc}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)

            # Still create a documents row so the job has a complete record
            # of what was submitted (and why this file failed).
            try:
                doc = create_document(
                    db=db,
                    job_id=job.id,
                    filename=safe_filename,
                    file_path=None,
                    file_type=file_type,
                    status="FAILED",
                    error_message=str(exc),
                )
                document_results.append(
                    DocumentResult(
                        document_id=doc.id,
                        filename=safe_filename,
                        status="FAILED",
                        error_message=str(exc),
                    )
                )
            except Exception as db_exc:
                # If even the error-recording write fails, log it but do not crash.
                logger.error(
                    f"Could not create FAILED document row for '{safe_filename}': {db_exc}",
                    exc_info=True,
                )

    # ── Step 3: Determine aggregate job status ───────────────────────────────
    succeeded = sum(1 for r in document_results if r.status == "INGESTED")
    failed = len(errors)

    if failed == 0:
        job_status = "INGESTED"
    elif succeeded == 0:
        job_status = "FAILED"
    else:
        job_status = "PARTIAL_FAILURE"

    update_job_status(db=db, job_id=job.id, status=job_status)

    # ── Step 4: Commit everything in one atomic transaction ──────────────────
    db.commit()
    logger.info(f"Job committed job_id={job.id} status={job_status}")

    # ── Step 5: Return structured response ──────────────────────────────────
    return JobCreateResponse(
        job_id=job.id,
        status=job_status,
        documents=document_results,
        errors=errors,
    )
