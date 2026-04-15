"""
app/api/router.py
──────────────────
Central API router — aggregates all route modules.

WHY a central router?
• app/main.py stays clean: it imports ONE router and calls app.include_router().
• Adding new route modules (e.g., /documents, /exports) only requires adding
  one line here, not touching main.py.
• Prefixes and tags can be applied consistently in one place.
"""

from fastapi import APIRouter

from app.api.routes import export, extract, health, jobs, review

# The root API router that main.py mounts.
api_router = APIRouter()

# Health / ops endpoints — no prefix, accessible at /health directly.
api_router.include_router(health.router)

# Job management endpoints.
api_router.include_router(jobs.router)

# Extraction pipeline endpoint.
api_router.include_router(extract.router)

# CSV export endpoint — GET /documents/{id}/export
api_router.include_router(export.router)

# Review API endpoint — GET /documents/{id}/results
api_router.include_router(review.router)
