"""Content rewriting service backed by structured model responses."""
import logging
import re
from typing import Optional
from datetime import datetime, timezone

from openai import AsyncOpenAI, RateLimitError, APIConnectionError, APITimeoutError
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx
import openai

from app.core.config import settings
from app.services.llm.circuit_breaker import llm_circuit_breaker

log = logging.getLogger(__name__)

HALLUCINATION_ZERO_PROMPT = """Rewrite the provided HTML content to improve its structure, clarity, and SEO quality.

CRITICAL CONSTRAINTS - "HALLUCINATION ZERO":
1. You may ONLY restructure, rephrase, and improve clarity.
2. You must NOT add any facts, claims, statistics, or information not present in the source.
3. Preserve ALL numbers, dates, names, URLs, and technical terms EXACTLY as they appear.
4. Do not invent quotes, testimonials, or references that don't exist.
5. Do not add new claims or expand on topics beyond what is stated.

STRUCTURAL IMPROVEMENTS ALLOWED:
1. Improve Hn structure (fix hierarchy: H1 → H2 → H3, no skipping levels)
2. Improve text density and readability
3. Reorganize paragraphs for better flow
4. Fix grammar and spelling errors
5. Strengthen existing calls-to-action (without inventing new ones)

DOM PRESERVATION RULES:
1. Preserve all HTML tag structure - do not remove, add, or restructure elements in ways that break page builders
2. Keep all CSS classes and IDs intact
3. Preserve all data-* attributes
4. Do not remove or merge existing elements
5. Do not add script tags or inline event handlers

Return ONLY the rewritten HTML with your improvements while strictly following these constraints.
"""

YMYL_CONSERVATIVE_PROMPT = """The following content comes from a YMYL (Your Money or Your Life) site.

⚠️ HIGH CAUTION MODE ACTIVATED ⚠️

This content can significantly impact readers' health, finances, or safety. You must be EXTREMELY conservative.

CRITICAL CONSTRAINTS - "HALLUCINATION ZERO":
1. You may ONLY fix obvious structural issues (broken heading hierarchy, missing H1).
2. Do NOT rephrase any substantive content - keep exact wording.
3. Preserve ALL numbers, dates, names, and technical specifications EXACTLY.
4. Do not add, remove, or modify any factual claims.

ONLY ALLOWED ACTIONS:
1. Fix H1 missing or multiple H1 issues
2. Fix heading hierarchy (H1 → H2 → H3)
3. Fix obvious HTML structure problems

DO NOT:
- Rephrase paragraphs
- Change word choices
- Add new content
- Modify any claims or statements

This rewrite MUST be flagged for human review regardless of confidence score.
"""


class RewriteResult(BaseModel):
    """Structured response returned by the content rewriter."""
    rewritten_html: str = Field(
        description="The rewritten HTML content with improved structure and clarity."
    )
    changes_summary: str = Field(
        description="Brief summary of changes made (e.g., 'Fixed H1 hierarchy, improved paragraph flow')."
    )
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence level of the rewrite quality (0.0 to 1.0)."
    )


class RewriteError(Exception):
    """Exception raised when rewriting fails."""
    pass


class ContentRewriter:
    """Rewrites content while preserving facts from the source material."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            log.warning("OPENAI_API_KEY is not set. Rewriting will fail.")
        self.client = AsyncOpenAI(api_key=self.api_key)

    def _truncate_content(self, content: str, max_chars: int = 50000) -> str:
        """Truncate content to avoid token overflow.

        Args:
            content: HTML content to truncate.
            max_chars: Maximum characters allowed (default from config).

        Returns:
            Truncated content with warning logged if truncation occurred.
        """
        if len(content) <= max_chars:
            return content

        log.warning(
            "Content truncated from %d to %d characters for LLM processing",
            len(content),
            max_chars,
        )
        return content[:max_chars]

    def _sanitize_html(self, html: str) -> str:
        """Basic HTML sanitization before sending to LLM.

        Removes potentially dangerous content and normalizes markup.

        Args:
            html: Raw HTML content.

        Returns:
            Sanitized HTML content.
        """
        sanitized = html

        script_pattern = r'<script[^>]*>.*?</script>'
        sanitized = re.sub(script_pattern, '', sanitized, flags=re.IGNORECASE | re.DOTALL)

        style_pattern = r'<style[^>]*>.*?</style>'
        sanitized = re.sub(style_pattern, '', sanitized, flags=re.IGNORECASE | re.DOTALL)

        event_pattern = r'\s+on\w+\s*=\s*["\'][^"\']*["\']'
        sanitized = re.sub(event_pattern, '', sanitized, flags=re.IGNORECASE)

        return sanitized.strip()

    def _get_system_prompt(self, conservative_mode: bool = False) -> str:
        """Get the appropriate system prompt based on mode.

        Args:
            conservative_mode: If True, use stricter YMYL prompt.

        Returns:
            System prompt string.
        """
        return YMYL_CONSERVATIVE_PROMPT if conservative_mode else HALLUCINATION_ZERO_PROMPT

    @retry(
        retry=retry_if_exception_type((
            httpx.RequestError,
            RateLimitError,
            APIConnectionError,
            APITimeoutError,
            openai.InternalServerError,
        )),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        stop=stop_after_attempt(5),
        reraise=True
    )
    async def rewrite(
        self,
        html_content: str,
        is_ymyl: bool = False,
        requires_manual_validation: bool = False,
        max_input_chars: Optional[int] = None,
        temperature: float = 0.0,
    ) -> RewriteResult:
        """Rewrite HTML content to improve IMS score while preserving facts.

        Args:
            html_content: The original HTML content to rewrite.
            is_ymyl: If True, use conservative YMYL mode.
            requires_manual_validation: If True, also use conservative mode.
            max_input_chars: Maximum input characters (overrides config).
            temperature: LLM temperature (default 0.0 for determinism).

        Returns:
            RewriteResult containing rewritten HTML, changes summary, and confidence.

        Raises:
            RewriteError: If rewriting fails or circuit is open.
            ValueError: If API key is missing.
        """
        if not self.api_key:
            raise ValueError("OpenAI API key is missing.")

        if llm_circuit_breaker.is_open():
            raise RewriteError(
                f"Circuit breaker is open. LLM API temporarily unavailable. "
                f"State: {llm_circuit_breaker.state_summary}"
            )

        conservative_mode = is_ymyl or requires_manual_validation

        max_chars = max_input_chars or getattr(settings, 'OPENAI_REWRITE_MAX_INPUT_CHARS', 50000)
        sanitized = self._sanitize_html(html_content)
        truncated = self._truncate_content(sanitized, max_chars)

        if not truncated.strip():
            raise RewriteError("Content is empty after sanitization")

        system_prompt = self._get_system_prompt(conservative_mode)

        if conservative_mode:
            log.info("Using YMYL conservative mode for rewrite")

        try:
            model = getattr(settings, 'OPENAI_REWRITE_MODEL', 'gpt-4o')

            response = await self.client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Original HTML Content:\n\n{truncated}"}
                ],
                response_format=RewriteResult,
                temperature=temperature,
            )

            result = response.choices[0].message.parsed
            if not result:
                raise RewriteError("Parsed message is empty.")

            if not result.rewritten_html or not result.rewritten_html.strip():
                raise RewriteError("Rewritten HTML is empty")

            llm_circuit_breaker.record_success()

            if conservative_mode:
                log.info(
                    "YMYL rewrite completed with confidence %.2f - requires manual validation",
                    result.confidence_score
                )
            else:
                log.info(
                    "Rewrite completed with confidence %.2f: %s",
                    result.confidence_score,
                    result.changes_summary[:100]
                )

            return result

        except (RateLimitError, APIConnectionError, APITimeoutError, openai.InternalServerError) as e:
            llm_circuit_breaker.record_failure()
            log.warning("Transient API error during rewrite (circuit state: %s): %s",
                       llm_circuit_breaker.state_summary, e)
            raise
        except Exception as e:
            log.error("Failed to rewrite content via OpenAI: %s", e)
            raise RewriteError(f"Rewriting failed: {e}") from e
