# app/db/models/__init__.py
# Importing all models here ensures they are registered in Base.metadata
# when any part of the codebase imports from app.db.models.
# This is required for SQLAlchemy to resolve relationships across models.

from app.db.models.base import Base  # noqa: F401
from app.db.models.jobs import Job  # noqa: F401
from app.db.models.documents import Document  # noqa: F401
from app.db.models.extraction_results import ExtractionResult  # noqa: F401
from app.db.models.consolidated_fields import ConsolidatedField  # noqa: F401
