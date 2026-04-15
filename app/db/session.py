"""
app/db/session.py
─────────────────
SQLAlchemy engine + session factory + FastAPI dependency.

WHY this pattern?
• `engine` is a connection pool — one per process, created once at startup.
• `SessionLocal` is a factory that produces individual DB sessions per request.
• `get_db()` is a FastAPI dependency injected into route handlers.  It opens
  a session, yields it to the route, then always closes it (even on errors)
  via the finally block.

USAGE in a route:
    from sqlalchemy.orm import Session
    from fastapi import Depends
    from app.db.session import get_db

    @router.post("/jobs")
    def create_job(db: Session = Depends(get_db)):
        ...
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# ── Engine ─────────────────────────────────────────────────────────────────────
# `create_engine` establishes the connection pool.
# pool_pre_ping=True sends a cheap "SELECT 1" before giving out a connection,
# which prevents errors after the DB goes away (e.g., Postgres restart).
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    # echo=True would log every SQL statement — useful for debugging but too noisy
    # for production; left off by default.
    echo=False,
)

# ── Session factory ────────────────────────────────────────────────────────────
# autocommit=False  → we manage transactions explicitly (call db.commit() ourselves).
# autoflush=False   → SQLAlchemy won't flush pending changes before every query,
#                     giving us full control over when writes hit the DB.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


# ── FastAPI dependency ─────────────────────────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """
    Yields a SQLAlchemy Session for the duration of one HTTP request.

    The `finally` block guarantees the session is closed even if the route
    handler raises an exception, preventing connection leaks.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
