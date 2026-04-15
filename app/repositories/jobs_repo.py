"""
app/repositories/jobs_repo.py
──────────────────────────────
Repository for the `jobs` table.

RESPONSIBILITY:
• All direct ORM reads/writes to the `jobs` table live here.
• Services call these functions instead of touching the DB directly.
  This keeps the service layer testable (swap the repo for a fake in tests)
  and prevents DB logic from leaking into route handlers.

PATTERN:
• Functions accept a `db: Session` as their first argument.
• Functions commit themselves (or callers commit) — here we do NOT commit
  inside the repo, because the service orchestrates the full transaction
  and decides when to commit.  The service calls db.commit() after all
  writes for a request are done.
"""

import uuid

from sqlalchemy.orm import Session

from app.db.models.jobs import Job


def create_job(db: Session, document_count: int) -> Job:
    """
    Insert a new Job row in PENDING status.

    Returns the ORM object (which now has db-generated defaults like created_at).
    The caller is responsible for calling db.commit() after all related writes.
    """
    job = Job(
        id=uuid.uuid4(),          # Generate PK in Python so we can use it immediately
        status="PENDING",
        document_count=document_count,
    )
    db.add(job)
    db.flush()   # flush sends the INSERT to Postgres within the current transaction
                 # so the row gets its server_default timestamps, but does NOT commit.
    return job


def update_job_status(db: Session, job_id: uuid.UUID, status: str) -> None:
    """
    Update the `status` column for an existing job.

    Called at the end of ingestion to mark the job as:
    • 'INGESTED'        — all files saved successfully
    • 'PARTIAL_FAILURE' — at least one file failed, others succeeded
    • 'FAILED'          — all files failed

    Does NOT commit — the caller controls the transaction boundary.
    """
    db.query(Job).filter(Job.id == job_id).update(
        {"status": status},
        synchronize_session="fetch",  # keeps the ORM session cache consistent
    )
