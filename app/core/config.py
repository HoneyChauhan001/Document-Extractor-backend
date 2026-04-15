"""
app/core/config.py
──────────────────
Centralised application configuration using Pydantic BaseSettings.

WHY Pydantic BaseSettings?
• Reads values from environment variables or a .env file automatically.
• Validates types at startup — fails fast if DATABASE_URL is missing.
• One place to change config; all modules import `settings` from here.

USAGE:
    from app.core.config import settings
    print(settings.DATABASE_URL)
"""

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Explicitly load .env into os.environ so that os.getenv() calls anywhere in
# the codebase (e.g., extractor feature flags) pick up the values from .env.
# pydantic-settings reads .env into the Settings model but does NOT populate
# os.environ — this call bridges that gap.
# Does not override real environment variables (correct: env vars > .env).
load_dotenv()


class Settings(BaseSettings):
    """
    All runtime configuration for the Doc-Extractor service.

    Values are loaded (in priority order):
    1. Actual environment variables (highest priority — good for Docker / CI)
    2. A .env file in the current working directory
    3. Default values defined below (lowest priority)
    """

    # ── Database ───────────────────────────────────────────────────────────────
    # Full PostgreSQL DSN.  Example: postgresql://user:pass@localhost:5432/dbname
    DATABASE_URL: str

    # ── File Storage ───────────────────────────────────────────────────────────
    # Root directory where uploaded contract files are written to disk.
    # Subdirectory structure: STORAGE_DIR/<job_id>/<filename>
    # The app creates this directory on startup if it does not exist.
    STORAGE_DIR: str = "./storage"

    # ── Logging ────────────────────────────────────────────────────────────────
    # Standard Python logging level string: DEBUG | INFO | WARNING | ERROR | CRITICAL
    LOG_LEVEL: str = "INFO"

    # ── Pydantic v2 config ─────────────────────────────────────────────────────
    # env_file tells BaseSettings to load from a .env file.
    # extra="ignore" silently discards any unknown env vars (prevents startup errors).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Module-level singleton — import this everywhere instead of re-instantiating.
# Instantiation reads and validates all env vars immediately.
settings = Settings()
