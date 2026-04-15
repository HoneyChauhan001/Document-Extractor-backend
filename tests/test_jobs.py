"""Smoke tests for POST /jobs (file upload + ingestion)."""
import uuid
from unittest.mock import patch

from app.schemas.documents import DocumentResult
from app.schemas.jobs import JobCreateResponse


# ── Helpers ────────────────────────────────────────────────────────────────────

def _pdf_file(name: str = "contract.pdf") -> tuple:
    return ("files", (name, b"%PDF-1.4 fake content", "application/pdf"))


def _docx_file(name: str = "contract.docx") -> tuple:
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return ("files", (name, b"PK fake docx content", mime))


def _make_success_response(
    job_id: uuid.UUID | None = None,
    doc_id: uuid.UUID | None = None,
    filename: str = "contract.pdf",
    status: str = "INGESTED",
) -> JobCreateResponse:
    job_id = job_id or uuid.uuid4()
    doc_id = doc_id or uuid.uuid4()
    return JobCreateResponse(
        job_id=job_id,
        status=status,
        documents=[DocumentResult(document_id=doc_id, filename=filename, status="INGESTED")],
        errors=[],
    )


# ── Validation: file count ─────────────────────────────────────────────────────

def test_create_job_too_many_files_returns_400(client):
    """More than 5 files → 400 Bad Request."""
    files = [_pdf_file(f"file{i}.pdf") for i in range(6)]
    response = client.post("/jobs", files=files)
    assert response.status_code == 400
    assert "5" in response.json()["detail"]


# ── Validation: file extension ─────────────────────────────────────────────────

def test_create_job_txt_extension_returns_400(client):
    """.txt file → 400 Bad Request."""
    files = [("files", ("contract.txt", b"plain text", "text/plain"))]
    response = client.post("/jobs", files=files)
    assert response.status_code == 400
    assert "contract.txt" in response.json()["detail"]


def test_create_job_xlsx_extension_returns_400(client):
    """.xlsx file → 400 Bad Request."""
    files = [("files", ("data.xlsx", b"fake xlsx", "application/vnd.ms-excel"))]
    response = client.post("/jobs", files=files)
    assert response.status_code == 400


def test_create_job_mixed_valid_invalid_returns_400(client):
    """One valid .pdf mixed with one invalid .csv → 400; invalid filename reported."""
    files = [
        _pdf_file("good.pdf"),
        ("files", ("bad.csv", b"col1,col2", "text/csv")),
    ]
    response = client.post("/jobs", files=files)
    assert response.status_code == 400
    assert "bad.csv" in response.json()["detail"]


# ── Happy paths ────────────────────────────────────────────────────────────────

def test_create_job_single_pdf_returns_201(client):
    """Single valid PDF → 201 with correct schema."""
    mock_resp = _make_success_response(filename="contract.pdf")

    with patch("app.services.ingestion.ingest_job", return_value=mock_resp):
        response = client.post("/jobs", files=[_pdf_file()])

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "INGESTED"
    assert len(body["documents"]) == 1
    assert body["documents"][0]["status"] == "INGESTED"
    assert body["errors"] == []
    uuid.UUID(body["job_id"])  # must be a valid UUID


def test_create_job_single_docx_returns_201(client):
    """Single valid DOCX → 201."""
    mock_resp = _make_success_response(filename="agreement.docx")

    with patch("app.services.ingestion.ingest_job", return_value=mock_resp):
        response = client.post("/jobs", files=[_docx_file("agreement.docx")])

    assert response.status_code == 201
    assert response.json()["status"] == "INGESTED"


def test_create_job_five_files_returns_201(client):
    """Maximum of 5 files → 201."""
    job_id = uuid.uuid4()
    docs = [
        DocumentResult(document_id=uuid.uuid4(), filename=f"f{i}.pdf", status="INGESTED")
        for i in range(5)
    ]
    mock_resp = JobCreateResponse(job_id=job_id, status="INGESTED", documents=docs, errors=[])

    with patch("app.services.ingestion.ingest_job", return_value=mock_resp):
        files = [_pdf_file(f"f{i}.pdf") for i in range(5)]
        response = client.post("/jobs", files=files)

    assert response.status_code == 201
    assert len(response.json()["documents"]) == 5


def test_create_job_partial_failure_returns_201(client):
    """Partial failure → 201 with PARTIAL_FAILURE status and errors in body."""
    job_id = uuid.uuid4()
    mock_resp = JobCreateResponse(
        job_id=job_id,
        status="PARTIAL_FAILURE",
        documents=[
            DocumentResult(document_id=uuid.uuid4(), filename="ok.pdf", status="INGESTED"),
            DocumentResult(
                document_id=uuid.uuid4(),
                filename="bad.pdf",
                status="FAILED",
                error_message="Disk write error",
            ),
        ],
        errors=["Failed to ingest 'bad.pdf': Disk write error"],
    )

    with patch("app.services.ingestion.ingest_job", return_value=mock_resp):
        files = [_pdf_file("ok.pdf"), _pdf_file("bad.pdf")]
        response = client.post("/jobs", files=files)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PARTIAL_FAILURE"
    assert len(body["errors"]) == 1


def test_create_job_response_has_required_fields(client):
    """Response schema always contains job_id, status, documents, errors."""
    mock_resp = _make_success_response()

    with patch("app.services.ingestion.ingest_job", return_value=mock_resp):
        response = client.post("/jobs", files=[_pdf_file()])

    body = response.json()
    for key in ("job_id", "status", "documents", "errors"):
        assert key in body, f"Missing key: {key}"
