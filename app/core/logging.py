"""
app/core/logging.py
────────────────────
Structured logging setup for the Doc-Extractor service.

WHY structured logging?
• Plain print() statements are hard to search and filter in production log aggregators.
• A consistent format (timestamp + level + module + message) makes debugging much faster.
• Using the stdlib `logging` module means any library that also uses `logging`
  (e.g., SQLAlchemy, uvicorn) is captured by the same configuration.

USAGE:
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Job created", extra={"job_id": str(job_id)})
"""

import logging
import sys

from app.core.config import settings


class _StructuredFormatter(logging.Formatter):
    """
    Custom formatter that writes log records as:
        2026-04-06T10:00:00.000Z | INFO     | app.services.ingestion | message

    This is human-readable locally and still easy to parse with log-aggregation
    tools (Splunk, Loki, CloudWatch Logs Insights) because fields are pipe-delimited.
    """

    FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    DATEFMT = "%Y-%m-%dT%H:%M:%S"

    def __init__(self) -> None:
        super().__init__(fmt=self.FMT, datefmt=self.DATEFMT)


def _configure_root_logger() -> None:
    """
    Configure the root logger once at import time.

    We attach a StreamHandler pointing to stdout (not stderr) so that log lines
    appear in the correct order alongside uvicorn's access logs when both are
    piped to the same sink.
    """
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StructuredFormatter())

    root = logging.getLogger()
    # Avoid adding duplicate handlers if this module is imported multiple times
    # (e.g., during pytest collection which reimports modules).
    if not root.handlers:
        root.addHandler(handler)

    root.setLevel(level)

    # Quieten noisy third-party loggers so they don't drown out app logs.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# Run configuration immediately on import so any module that calls get_logger()
# before main.py starts will still get a properly configured logger.
_configure_root_logger()


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger.

    Prefer calling this at module level:
        logger = get_logger(__name__)

    Using __name__ automatically gives each module its own logger hierarchy
    (e.g., "app.services.ingestion") which makes filtering log output easy.
    """
    return logging.getLogger(name)
