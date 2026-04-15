"""
app/extractors/nvidia_extractor.py
────────────────────────────────────
NVIDIA AI (NIM) extractor — LLM-based field extraction using NVIDIA's
OpenAI-compatible inference API (https://integrate.api.nvidia.com/v1).

CURRENT STATE: Functional.
API calls are gated behind NVIDIA_ENABLED=true + NVIDIA_API_KEY in .env.

HOW IT WORKS:
1. Extract raw text from the document (PDF via pdfplumber, DOCX via python-docx).
2. Send the same strict JSON system prompt used by GPT/Claude to the NVIDIA NIM endpoint.
3. Parse the JSON response deterministically using the shared _parse_llm_json helper.
4. Any JSON parse failure → all fields null + error recorded.

WHY NVIDIA NIM?
- OpenAI-compatible REST API — reuses the existing `openai` package, no new dependency.
- Free tier available for models like abacusai/dracarys-llama-3.1-70b-instruct.
- Acts as an additional LLM vote in the consolidation agreement step.

HOW TO ENABLE:
1. Sign up at https://integrate.api.nvidia.com and get an API key
2. Add to .env:
     NVIDIA_ENABLED=true
     NVIDIA_API_KEY=nvapi-...
     NVIDIA_MODEL=abacusai/dracarys-llama-3.1-70b-instruct   # optional override
3. Restart the app — no code changes needed.

PRIORITY in consolidation tie-breaking: after GPT and Claude (LLM tier),
before OCR. Position 5 in the canonical EXTRACTION_METHODS order.
"""

import os
import uuid
from typing import Optional

from app.core.logging import get_logger
from app.extractors.base import V1_FIELDS, BaseExtractor, ExtractionResult
from app.extractors.gpt_extractor import (
    _SYSTEM_PROMPT,
    _parse_llm_json,
    _post_process_results,
    _read_document_text,
    _smart_chunk,
)

logger = get_logger(__name__)

_MODEL = os.getenv("NVIDIA_MODEL", "abacusai/dracarys-llama-3.1-70b-instruct")
_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NvidiaExtractor(BaseExtractor):
    """
    Extracts V1 fields by prompting an NVIDIA-hosted LLM via the NIM API.

    Uses the OpenAI-compatible client (the `openai` package) pointed at
    NVIDIA's base URL — no extra dependency required.

    Priority in consolidation: LLM tier (after GPT + Claude, before OCR).
    """

    method = "nvidia"

    def extract(
        self,
        document_id: uuid.UUID,
        file_path: str,
        file_type: str,
    ) -> list[ExtractionResult]:
        if not os.getenv("NVIDIA_ENABLED", "false").lower() == "true":
            logger.info(
                f"NVIDIA skipped for document_id={document_id} (NVIDIA_ENABLED=false)"
            )
            return self._null_results(
                document_id,
                error_code="NOT_ENABLED",
                error_message="Set NVIDIA_ENABLED=true and provide NVIDIA_API_KEY",
            )

        api_key = os.getenv("NVIDIA_API_KEY", "")
        if not api_key:
            logger.warning(
                f"NVIDIA_API_KEY not set — skipping document_id={document_id}"
            )
            return self._null_results(
                document_id,
                error_code="NO_API_KEY",
                error_message="NVIDIA_API_KEY is not configured",
            )

        # ── Read document text ─────────────────────────────────────────────────
        doc_text = _read_document_text(file_path, file_type)
        if not doc_text:
            return self._null_results(
                document_id,
                error_code="EMPTY_DOCUMENT",
                error_message="Could not extract text from document",
            )

        # ── Call NVIDIA NIM via OpenAI-compatible client ───────────────────────
        try:
            import openai  # already in requirements.txt
            client = openai.OpenAI(
                base_url=_BASE_URL,
                api_key=api_key,
            )
            # NOTE: response_format={"type": "json_object"} is NOT used here because
            # not all NVIDIA-hosted models support it. The strict system prompt
            # already instructs the model to return only valid JSON — same guarantee
            # the Claude extractor relies on without response_format.
            response = client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Contract text:\n\n{_smart_chunk(doc_text)}",
                    },
                ],
                temperature=0,   # deterministic output
                top_p=0.7,       # NVIDIA NIM recommended default
                max_tokens=512,  # JSON with 5 short fields needs very few tokens
                timeout=45,      # slightly longer than GPT — NIM cold starts can be slow
            )
            raw_content = response.choices[0].message.content or "{}"

            # Some models wrap the JSON in markdown fences — strip them.
            raw_json = _strip_markdown_fences(raw_content)

        except Exception as exc:
            logger.error(
                f"NVIDIA API call failed for document_id={document_id}: {exc}"
            )
            return self._null_results(
                document_id,
                error_code="API_ERROR",
                error_message=str(exc),
            )

        # ── Parse + post-process ───────────────────────────────────────────────
        results = _parse_llm_json(raw_json, document_id, self.method)
        return _post_process_results(results)

    def _null_results(
        self,
        document_id: uuid.UUID,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> list[ExtractionResult]:
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


def _strip_markdown_fences(text: str) -> str:
    """
    Remove ```json ... ``` or ``` ... ``` markdown fences if present.

    Some NVIDIA-hosted models (especially instruction-tuned Llama variants)
    wrap JSON responses in markdown code blocks despite being told not to.
    This strips the outer fences and returns the inner content.
    """
    import re
    text = text.strip()
    # Match ```json or ``` at start, ``` at end
    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text
