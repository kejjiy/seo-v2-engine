"""Website classification service backed by structured model responses."""
import logging
from typing import Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx
import openai

from app.core.config import settings

log = logging.getLogger(__name__)

# Classification instructions
CLASSIFICATION_PROMPT = """Analyze the text extracted from a website's homepage and 'About' page.
Based ONLY on this text, you must determine two things:
1. The primary industry or 'sector' of the website (e.g., "Medical", "Plumbing", "E-commerce", "Finance", "Legal", "General Blog", etc.). Be concise (1-3 words).
2. Whether the website falls under the YMYL (Your Money or Your Life) category.

A website is YMYL if its content can significantly impact a person's future happiness, health, financial stability, or safety.
Examples of YMYL topics include:
- Medical advice, hospitals, pharmacies
- Financial advice, banking, insurance, taxes
- Legal services, government information
- News articles on critical topics
- E-commerce sites facilitating huge transactions

Respond ONLY with the strict structured output requested. Do not hallucinate. If the text does not contain enough information, make your best educated guess for the sector based on clues, but default is_ymyl to false unless there is clear evidence of YMYL topics.
"""

class SiteClassification(BaseModel):
    """Structured response returned by the classifier."""
    sector: str = Field(description="The primary industry or sector of the website (e.g. 'Medical', 'Plumbing').")
    is_ymyl: bool = Field(description="True if the site operates in a YMYL (Your Money or Your Life) sensitive area.")

class ClassificationAgent:
    """Classifies sites by sector and content sensitivity."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            log.warning("OPENAI_API_KEY is not set. Classification will fail or use default.")
        self.client = AsyncOpenAI(api_key=self.api_key)

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, openai.RateLimitError, openai.InternalServerError)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True
    )
    async def classify_site(self, text_content: str) -> SiteClassification:
        """Classify website text content to determine sector and YMYL status.

        Args:
            text_content: Stripped text from the homepage and about page.

        Returns:
            SiteClassification containing sector and is_ymyl.
        """
        if not self.api_key:
            raise ValueError("OpenAI API key is missing.")

        # Truncate content to avoid extreme token usage if too large
        # 15000 chars is roughly ~3000 tokens which is enough for homepage+about
        max_chars = 15000
        truncated_text = text_content[:max_chars]

        try:
            # Using gpt-4o as requested for structured parsing
            response = await self.client.beta.chat.completions.parse(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": CLASSIFICATION_PROMPT},
                    {"role": "user", "content": f"Website Content:\n\n{truncated_text}"}
                ],
                response_format=SiteClassification,
                temperature=0.0,
                max_tokens=100
            )

            result = response.choices[0].message.parsed
            if not result:
                 raise RuntimeError("Parsed message is empty.")

            return result

        except Exception as e:
            log.error(f"Failed to classify site via OpenAI: {e}")
            raise
