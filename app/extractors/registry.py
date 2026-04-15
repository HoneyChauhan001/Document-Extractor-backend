"""
app/extractors/registry.py
───────────────────────────
Extractor registry — single source of truth for which extractors exist
and the order they run in.

WHY a registry?
• The orchestrator asks the registry for the extractor list instead of
  hard-coding it.  Adding a new extractor = add one line here.
• Order is deterministic: extractors always run in REGISTRY_ORDER,
  which matches EXTRACTION_METHODS in base.py.
• Consolidation tie-breaking also depends on this order (Textract first).

IMPORTANT: The order here must match EXTRACTION_METHODS in base.py.
"""

from app.extractors.base import BaseExtractor
from app.extractors.textract_extractor import TextractExtractor
from app.extractors.ocr_extractor import OCRExtractor
from app.extractors.gpt_extractor import GPTExtractor
from app.extractors.claude_extractor import ClaudeExtractor
from app.extractors.nvidia_extractor import NvidiaExtractor

# Ordered list of extractor instances.
# This is the CANONICAL order — do not change without updating EXTRACTION_METHODS.
# 1. textract — highest priority in tie-breaking
# 2. ocr      — local fallback
# 3. gpt      — LLM (OpenAI)
# 4. claude   — LLM (Anthropic)
# 5. nvidia   — LLM (NVIDIA NIM — OpenAI-compatible free tier)
EXTRACTORS: list[BaseExtractor] = [
    TextractExtractor(),
    OCRExtractor(),
    GPTExtractor(),
    ClaudeExtractor(),
    NvidiaExtractor(),
]
