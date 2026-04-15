"""
app/api/routes/health.py
─────────────────────────
GET /health — simple liveness check.

PURPOSE:
• Lets Kubernetes/Docker health probes confirm the service is alive.
• Does NOT check DB connectivity (that would be a "readiness" probe — add later).
• Intentionally minimal: no auth, no DB, no business logic.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["ops"])
def health_check() -> dict:
    """
    Liveness probe endpoint.

    Returns HTTP 200 with a static body as long as the process is running.
    No database or downstream checks — those belong in a separate /ready endpoint.
    """
    return {"status": "ok"}
