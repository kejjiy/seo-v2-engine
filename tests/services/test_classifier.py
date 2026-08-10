import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import openai
from pydantic import BaseModel

from app.services.llm.classifier import ClassificationAgent, SiteClassification

pytestmark = pytest.mark.asyncio

class MockParsedMessage(BaseModel):
    parsed: SiteClassification

class MockChoice(BaseModel):
    message: MockParsedMessage

class MockResponse(BaseModel):
    choices: list[MockChoice]


@pytest.fixture
def mock_openai_parse():
    with patch("app.services.llm.classifier.AsyncOpenAI") as mock_openai:
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_parse = AsyncMock()
        mock_client.beta.chat.completions.parse = mock_parse
        yield mock_parse


async def test_classify_site_ymyl(mock_openai_parse):
    """Test classification for a medical YMYL site."""
    mock_response = MockResponse(
        choices=[MockChoice(message=MockParsedMessage(parsed=SiteClassification(sector="Medical", is_ymyl=True)))]
    )
    mock_openai_parse.return_value = mock_response

    agent = ClassificationAgent(api_key="sk-test")
    # Provide sample medical text
    text_content = "We provide the best cardiology treatments and heart surgery."

    result = await agent.classify_site(text_content)

    assert result.sector == "Medical"
    assert result.is_ymyl is True

    mock_openai_parse.assert_called_once()
    kwargs = mock_openai_parse.call_args.kwargs
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["response_format"] == SiteClassification
    assert "heart surgery" in kwargs["messages"][1]["content"]


async def test_classify_site_non_ymyl(mock_openai_parse):
    """Test classification for a generic e-commerce site."""
    mock_response = MockResponse(
        choices=[MockChoice(message=MockParsedMessage(parsed=SiteClassification(sector="Sports Equipment", is_ymyl=False)))]
    )
    mock_openai_parse.return_value = mock_response

    agent = ClassificationAgent(api_key="sk-test")
    text_content = "Buy the best basketballs and sneakers here."

    result = await agent.classify_site(text_content)

    assert result.sector == "Sports Equipment"
    assert result.is_ymyl is False


async def test_classify_site_retry_on_rate_limit(mock_openai_parse):
    """Test that the classifier retries on rate limit errors."""
    mock_response = MockResponse(
        choices=[MockChoice(message=MockParsedMessage(parsed=SiteClassification(sector="Blog", is_ymyl=False)))]
    )

    # Configure mock to raise RateLimitError then succeed
    mock_openai_parse.side_effect = [
        openai.RateLimitError(
            message="Rate limit reached",
            response=httpx.Response(status_code=429, request=httpx.Request("POST", "uri")),
            body=None
        ),
        mock_response
    ]

    agent = ClassificationAgent(api_key="sk-test")

    # H4 fix: Override the retry's wait strategy on the method instance
    # to avoid sleeping during tests. This is the idiomatic tenacity approach.
    from tenacity import wait_none
    agent.classify_site.retry.wait = wait_none()

    result = await agent.classify_site("Test content")

    assert result.sector == "Blog"
    assert mock_openai_parse.call_count == 2
