# ── Stage 1: builder — install Python deps ────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim

# System dependencies:
# - tesseract-ocr    : OCR engine required by pytesseract (OCRExtractor)
# - poppler-utils    : PDF → PIL rendering, required by pdf2image (OCRExtractor)
# - libpq-dev        : PostgreSQL client library C headers for psycopg
# - postgresql-client: psql binary used by the Helm db-init hook job
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
        libpq-dev \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from builder layer
COPY --from=builder /install /usr/local

# Download spaCy English model for NER (OCRExtractor uses it for person_name + company_name)
RUN python -m spacy download en_core_web_sm

# Copy application source last — keeps code changes from busting dep cache
COPY . .

EXPOSE 8000

# Runs as non-root user for security
RUN adduser --disabled-password --gecos "" appuser
USER appuser

# Shell form so ${PORT:-8000} is expanded at runtime.
# Render injects $PORT (usually 10000); falls back to 8000 for local docker run.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
