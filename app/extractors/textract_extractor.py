"""
app/extractors/textract_extractor.py
──────────────────────────────────────
Amazon Textract extractor — cloud OCR with form/key-value analysis.

CURRENT STATE: Functional stub.
Returns None for all fields with method_confidence=None.
Real implementation is gated behind TEXTRACT_ENABLED=true in .env so the
rest of the pipeline works end-to-end before AWS credentials are configured.

HOW TO ENABLE (Step 2, real implementation):
1. Set TEXTRACT_ENABLED=true and AWS_REGION in .env
2. Add boto3 to requirements.txt
3. Replace the stub body in `_call_textract()` with the real AWS API call:
   - Use AnalyzeDocument (FORMS + TABLES) for PDFs already in S3, or
   - Use start_document_analysis for async large-doc processing.
4. Parse KeyValueSet blocks → map to V1 fields via keyword matching.

WHY Textract gets priority in tie-breaking (from consolidation rules):
Textract runs purpose-built ML on document structure (forms, tables, key-value
pairs) rather than raw text, so it typically has higher layout fidelity than
plain OCR for contract-format documents.
"""

import os
import uuid
from typing import Optional

from app.core.logging import get_logger
from app.extractors.base import V1_FIELDS, BaseExtractor, ExtractionResult

# Read AWS credentials from environment at module load time.
# These are pseudo values in .env during development — real values in production.
_AWS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
_AWS_SECRET = os.getenv("AWS_SECRET_ACCESS_KEY")
_AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# V1 field → list of lowercase keyword substrings to scan for in Textract KEY blocks.
# The first key that contains any of these substrings is mapped to that field.
_FIELD_KEYWORDS: dict[str, list[str]] = {
    "person_name":    ["name", "party", "individual", "employee", "contractor", "signed by", "signatory"],
    "company_name":   ["company", "corporation", "entity", "employer", "client", "organization", "firm", "inc", "llc"],
    "contract_date":  ["date", "effective date", "agreement date", "dated", "execution date"],
    "contract_value": ["value", "amount", "consideration", "total", "price", "fee", "compensation", "payment"],
    "address":        ["address", "located at", "principal place", "place of business", "street"],
}

logger = get_logger(__name__)

# Feature flag: set TEXTRACT_ENABLED=true in .env to activate real AWS calls.
# Read inside extract() at call time (not module level) so .env changes take
# effect without restarting the server and import-order issues are avoided.


def _get_block_text(block: dict, block_map: dict[str, dict]) -> str:
    """
    Recursively assemble the display text for a Textract block.

    Textract stores text split across WORD child blocks.  A KEY or VALUE block
    references its constituent WORD blocks via a RELATIONSHIPS[type=CHILD] list.
    We walk those children and join their Text fields.
    """
    words: list[str] = []
    for rel in block.get("Relationships", []):
        if rel["Type"] == "CHILD":
            for child_id in rel["Ids"]:
                child = block_map.get(child_id, {})
                if child.get("BlockType") in ("WORD", "LINE"):
                    words.append(child.get("Text", ""))
    return " ".join(words)


class TextractExtractor(BaseExtractor):
    """
    Extracts V1 fields using Amazon Textract AnalyzeDocument.

    Priority in consolidation tie-breaking: HIGHEST (preferred over OCR/LLMs
    when no agreement is reached across methods).
    """

    method = "textract"

    def extract(
        self,
        document_id: uuid.UUID,
        file_path: str,
        file_type: str,
    ) -> list[ExtractionResult]:
        """
        Run Textract extraction on the document.

        When TEXTRACT_ENABLED=false (default), returns nulls for all fields
        and records error_code='NOT_ENABLED' so the consolidation layer knows
        this method was attempted but skipped, not that it failed unexpectedly.
        """
        _enabled = os.getenv("TEXTRACT_ENABLED", "false").lower() == "true"
        if not _enabled:
            logger.info(
                f"Textract skipped for document_id={document_id} "
                f"(TEXTRACT_ENABLED=false)"
            )
            return self._null_results(document_id, error_code="NOT_ENABLED",
                                      error_message="Set TEXTRACT_ENABLED=true to activate")

        # ── Real Textract call ─────────────────────────────────────────────────
        return self._run_textract(document_id, file_path, file_type)

    def _run_textract(
        self,
        document_id: uuid.UUID,
        file_path: str,
        file_type: str,
    ) -> list[ExtractionResult]:
        """
        Call AWS Textract AnalyzeDocument and map KEY → VALUE blocks to V1 fields.

        With pseudo credentials this will raise a ClientError (auth failure),
        which is caught and returned as API_ERROR rows — pipeline continues.
        """
        try:
            import boto3  # lazy import — only needed when TEXTRACT_ENABLED=true
            from botocore.exceptions import ClientError
        except ImportError:
            return self._null_results(
                document_id,
                error_code="DEPENDENCY_MISSING",
                error_message="boto3 not installed. Run: pip install boto3",
            )

        # Read the file bytes — Textract synchronous API accepts raw bytes for
        # documents up to 10 MB.  Larger docs require S3 + async API.
        try:
            with open(file_path, "rb") as fh:
                file_bytes = fh.read()
        except OSError as exc:
            return self._null_results(
                document_id,
                error_code="FILE_READ_ERROR",
                error_message=str(exc),
            )

        # ── Call Textract ──────────────────────────────────────────────────────
        try:
            client = boto3.client(
                "textract",
                region_name=_AWS_REGION,
                aws_access_key_id=_AWS_KEY,
                aws_secret_access_key=_AWS_SECRET,
            )
            response = client.analyze_document(
                Document={"Bytes": file_bytes},
                FeatureTypes=["FORMS", "TABLES"],
            )
        except Exception as exc:  # ClientError, EndpointResolutionError, etc.
            logger.warning(
                f"Textract API error document_id={document_id}: {exc}"
            )
            return self._null_results(
                document_id,
                error_code="API_ERROR",
                error_message=str(exc),
            )

        # ── Parse KEY_VALUE_SET blocks ─────────────────────────────────────────
        # Textract returns a flat list of Blocks.  KEY_VALUE_SET blocks come in
        # pairs: KEY blocks (contain the label text) and VALUE blocks (the value).
        # Each KEY block has a RELATIONSHIPS entry of type "VALUE" pointing to the
        # matching VALUE block id.  VALUE blocks point to WORD/LINE blocks via
        # a "CHILD" relationship.
        blocks = response.get("Blocks", [])
        block_map: dict[str, dict] = {b["Id"]: b for b in blocks}

        # Build key→value text map from KEY_VALUE_SET pairs.
        kv_pairs: list[tuple[str, str]] = []
        for block in blocks:
            if block.get("BlockType") != "KEY_VALUE_SET":
                continue
            if "KEY" not in block.get("EntityTypes", []):
                continue

            key_text = _get_block_text(block, block_map)
            if not key_text:
                continue

            # Find the paired VALUE block via RELATIONSHIPS.
            value_text = ""
            for rel in block.get("Relationships", []):
                if rel["Type"] == "VALUE":
                    for val_block_id in rel["Ids"]:
                        val_block = block_map.get(val_block_id, {})
                        value_text = _get_block_text(val_block, block_map)
                        break

            kv_pairs.append((key_text.strip(), value_text.strip()))

        # ── Map kv_pairs to V1 fields using keyword heuristics ─────────────────
        # Priority: first matching key wins per field; unmatched fields → None.
        extracted: dict[str, Optional[str]] = {f: None for f in V1_FIELDS}
        for key_text, value_text in kv_pairs:
            key_lower = key_text.lower()
            for field_name, keywords in _FIELD_KEYWORDS.items():
                if extracted[field_name] is not None:
                    continue  # already filled, skip
                if any(kw in key_lower for kw in keywords):
                    extracted[field_name] = value_text or None

        logger.info(
            f"Textract extracted document_id={document_id} "
            f"fields_found={sum(1 for v in extracted.values() if v)}/{len(V1_FIELDS)}"
        )

        return [
            ExtractionResult(
                document_id=document_id,
                method=self.method,
                field=f,
                value=extracted.get(f),
            )
            for f in V1_FIELDS
        ]

    def _null_results(
        self,
        document_id: uuid.UUID,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> list[ExtractionResult]:
        """Return one null ExtractionResult per V1 field (stable order)."""
        return [
            ExtractionResult(
                document_id=document_id,
                method=self.method,
                field=f,
                value=None,
                error_code=error_code,
                error_message=error_message,
            )
            for f in V1_FIELDS
        ]
