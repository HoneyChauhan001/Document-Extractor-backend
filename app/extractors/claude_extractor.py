"""
app/extractors/claude_extractor.py
────────────────────────────────────
Anthropic Claude extractor — LLM-based field extraction with strict JSON output.

CURRENT STATE: Functional stub.
Real calls are gated behind ANTHROPIC_API_KEY being set in .env.

HOW TO ENABLE:
1. pip install anthropic pdfplumber python-docx
2. Add to .env: ANTHROPIC_API_KEY=sk-ant-...  CLAUDE_MODEL=claude-3-5-sonnet-20241022
3. Set CLAUDE_ENABLED=true in .env

Uses the same JSON prompt contract and _parse_llm_json helper as GPTExtractor
to ensure output is identical in shape regardless of which LLM produced it.
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

# Read CLAUDE_ENABLED + model at call time — not module level.
_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")  # safe at module level


class ClaudeExtractor(BaseExtractor):
    """
    Extracts V1 fields by prompting Anthropic Claude with the same strict JSON prompt
    used by GPTExtractor — ensuring consistent output shape across LLMs.

    Priority in consolidation: same tier as GPT (LLM methods).
    """

    method = "claude"

    def extract(
        self,
        document_id: uuid.UUID,
        file_path: str,
        file_type: str,
    ) -> list[ExtractionResult]:
        if not os.getenv("CLAUDE_ENABLED", "false").lower() == "true":
            logger.info(
                f"Claude skipped for document_id={document_id} (CLAUDE_ENABLED=false)"
            )
            return self._null_results(
                document_id,
                error_code="NOT_ENABLED",
                error_message="Set CLAUDE_ENABLED=true and provide ANTHROPIC_API_KEY",
            )

        # ── Read document text ─────────────────────────────────────────────────
        doc_text = _read_document_text(file_path, file_type)
        if not doc_text:
            return self._null_results(document_id, error_code="EMPTY_DOCUMENT",
                                      error_message="Could not extract text from document")

        # ── Call Claude ────────────────────────────────────────────────────────
        try:
            import anthropic  # imported lazily — only needed when enabled
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment
            message = client.messages.create(
                model=_MODEL,
                max_tokens=512,        # JSON with 5 short fields needs very few tokens
                temperature=0,         # deterministic output
                system=_SYSTEM_PROMPT, # same prompt contract as GPT
                messages=[
                    {
                        "role": "user",
                        # Smart chunk: first 4k + last 4k — same strategy as GPT.
                        "content": f"Contract text:\n\n{_smart_chunk(doc_text)}",
                    }
                ],
            )
            raw_json = message.content[0].text if message.content else "{}"
        except Exception as exc:
            logger.error(f"Claude API call failed for document_id={document_id}: {exc}")
            return self._null_results(document_id, error_code="API_ERROR",
                                      error_message=str(exc))

        # ── Parse + post-process (normalize dates/currency) ────────────────────
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
