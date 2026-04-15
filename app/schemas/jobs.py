"""
app/schemas/jobs.py
────────────────────
Pydantic schemas for Job-level API I/O.

Covers:
  • JobCreateResponse — returned by POST /jobs

Later steps will add schemas for extraction results, consolidation, etc.
"""

import uuid

from pydantic import BaseModel

from app.schemas.documents import DocumentResult


class JobCreateResponse(BaseModel):
    """
    Response body returned by POST /jobs (HTTP 201 Created).

    Fields:
      job_id    — UUID of the newly created job row.
      status    — Aggregate job status: 'INGESTED' or 'PARTIAL_FAILURE'.
      documents — One entry per uploaded file:
                    • status='INGESTED' → file saved + DB row created.
                    • status='FAILED'   → file could not be processed;
                                          error_message explains why.
      errors    — Shorthand list of error strings for any failed files.
                  Callers can check `len(errors) > 0` to detect partial failure
                  without iterating documents[].
    """

    job_id: uuid.UUID
    status: str
    documents: list[DocumentResult]
    errors: list[str]

    model_config = {"from_attributes": True}
