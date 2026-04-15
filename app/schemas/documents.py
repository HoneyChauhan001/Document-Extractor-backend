"""
app/schemas/documents.py
─────────────────────────
Pydantic schemas for Document-level data in API responses.

WHY separate schemas from ORM models?
• ORM models are tied to the DB structure (columns, FK relationships).
• Schemas define what the API exposes — they can include computed fields,
  rename columns, or omit internal fields without changing the DB model.
• This separation prevents accidental leakage of internal DB fields to callers.
"""

import uuid
from typing import Literal

from pydantic import BaseModel


# The set of per-document statuses the API surface exposes to callers.
DocumentStatus = Literal["INGESTED", "FAILED"]


class DocumentResult(BaseModel):
    """
    Represents one document within a job's response.

    Returned as part of JobCreateResponse.documents[] after POST /jobs.
    """

    document_id: uuid.UUID
    filename: str
    status: str  # 'INGESTED' or 'FAILED'

    # Populated only if status == 'FAILED' — tells the caller what went wrong
    # with this specific file without failing the whole job response.
    error_message: str | None = None

    # Pydantic v2: allow constructing this schema from a SQLAlchemy ORM object
    # (i.e., model.model_validate(orm_obj)) without calling __init__ manually.
    model_config = {"from_attributes": True}
