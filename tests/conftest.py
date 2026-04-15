"""
tests/conftest.py
──────────────────
Shared fixtures for the Doc-Extractor smoke test suite.

DATABASE_URL must exist in os.environ BEFORE any app.* module is imported,
because app/core/config.py calls Settings() at module level (import time).
os.environ.setdefault() runs before any other import here, so it always wins
over load_dotenv() — which never overwrites an already-set variable.
"""
import os

# ── Must be first — before ANY app.* import ───────────────────────────────────
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@localhost:5432/testdb",
)

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app


@pytest.fixture()
def mock_db() -> MagicMock:
    """
    Return a fresh MagicMock standing in for a SQLAlchemy Session.

    MagicMock auto-creates chained attributes, so calls like:
        db.query(Job).filter(...).first()
    work without explicit setup (return a truthy MagicMock by default).

    Individual tests override .return_value chains to control responses.
    """
    return MagicMock()


@pytest.fixture()
def client(mock_db: MagicMock) -> TestClient:
    """
    Return a TestClient with get_db overridden to yield the mock session.

    The context-manager form triggers the app lifespan (creating ./storage if
    absent — already exists, so it is a no-op).
    dependency_overrides is cleared after each test to prevent bleed-through.
    """

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
