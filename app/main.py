"""
app/main.py
────────────
FastAPI application entry point.

WHAT THIS FILE DOES:
1. Creates the FastAPI application instance with title and version metadata.
2. Registers a startup event that ensures the STORAGE_DIR exists on disk.
3. Mounts the central API router (all routes are defined in app/api/).
4. That's it — no business logic lives here.

Thin main.py is intentional (fastapi-service-patterns SKILL):
• Routes delegate to services.
• Services call repositories.
• main.py just wires them together.

RUN WITH:
    uvicorn app.main:app --reload --port 8000

ENVIRONMENT:
    Copy .env.example to .env, then set DATABASE_URL to your Postgres DSN.
    Run `psql $DATABASE_URL -f sql/init.sql` to create tables before starting.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler (FastAPI v0.93+ recommended pattern).

    Code before `yield` runs on startup; code after `yield` runs on shutdown.
    Using lifespan instead of deprecated @app.on_event("startup") is the
    current FastAPI best practice.
    """
    # ── Startup ────────────────────────────────────────────────────────────────
    # Ensure the root storage directory exists.
    # Uploaded files go into: STORAGE_DIR/<job_id>/<filename>
    storage_path = Path(settings.STORAGE_DIR)
    storage_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Storage directory ready at: {storage_path.resolve()}")
    logger.info("Doc-Extractor service starting up — MVP v1.1")

    yield  # App is running here

    # ── Shutdown ───────────────────────────────────────────────────────────────
    # Nothing to clean up for V1 (SQLAlchemy closes connections via the pool).
    logger.info("Doc-Extractor service shutting down")


# Create the FastAPI app instance.
app = FastAPI(
    title="Doc-Extractor API",
    description=(
        "Multi-document contract extraction service. "
        "Extracts: person_name, company_name, contract_date, contract_value, address."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# CORS — allow the Vite dev server to call this API from the browser.
# Both localhost and 127.0.0.1 are listed because browsers treat them as
# different origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routes.  The api_router aggregates /health, /jobs, etc.
# No global prefix here — routes define their own paths.
# If a versioned prefix is needed later, add: prefix="/api/v1"
app.include_router(api_router)
