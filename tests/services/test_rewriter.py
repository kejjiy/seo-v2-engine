"""Tests for the LLM Content Rewriter Service (Story 3.6)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

import httpx
import openai
from pydantic import BaseModel

from app.services.llm.rewriter import (
    ContentRewriter,
    RewriteResult,
    RewriteError,
    HALLUCINATION_ZERO_PROMPT,
    YMYL_CONSERVATIVE_PROMPT,
)
from app.services.llm.circuit_breaker import CircuitBreaker, CircuitState

pytestmark = pytest.mark.asyncio


class MockParsedMessage(BaseModel):
    parsed: RewriteResult


class MockChoice(BaseModel):
    message: MockParsedMessage


class MockResponse(BaseModel):
    choices: list[MockChoice]


@pytest.fixture
def mock_openai_parse():
    with patch("app.services.llm.rewriter.AsyncOpenAI") as mock_openai:
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_parse = AsyncMock()
        mock_client.beta.chat.completions.parse = mock_parse
        yield mock_parse


@pytest.fixture
def sample_html():
    return """
    <html>
    <head><title>Test Page</title></head>
    <body>
        <h2>Section 1</h2>
        <p>This is some test content with numbers like 42 and dates like 2024-01-15.</p>
        <h3>Subsection</h3>
        <p>More content here.</p>
    </body>
    </html>
    """


@pytest.fixture
def fresh_circuit_breaker():
    return CircuitBreaker(
        failure_threshold=3,
        cooldown_seconds=60,
        window_seconds=120,
    )


async def test_normal_rewrite_flow(mock_openai_parse, sample_html):
    """Test normal rewrite flow returns expected structured output."""
    mock_response = MockResponse(
        choices=[
            MockChoice(
                message=MockParsedMessage(
                    parsed=RewriteResult(
                        rewritten_html="<html><body><h1>Fixed</h1></body></html>",
                        changes_summary="Added H1, fixed hierarchy",
                        confidence_score=0.85,
                    )
                )
            )
        ]
    )
    mock_openai_parse.return_value = mock_response

    rewriter = ContentRewriter(api_key="sk-test")
    result = await rewriter.rewrite(sample_html)

    assert result.rewritten_html == "<html><body><h1>Fixed</h1></body></html>"
    assert result.changes_summary == "Added H1, fixed hierarchy"
    assert result.confidence_score == 0.85

    mock_openai_parse.assert_called_once()
    kwargs = mock_openai_parse.call_args.kwargs
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["response_format"] == RewriteResult
    assert kwargs["temperature"] == 0.0


async def test_ymyl_conservative_mode(mock_openai_parse, sample_html):
    """Test that YMYL mode uses conservative prompt."""
    mock_response = MockResponse(
        choices=[
            MockChoice(
                message=MockParsedMessage(
                    parsed=RewriteResult(
                        rewritten_html=sample_html,
                        changes_summary="Minimal structural fixes only",
                        confidence_score=0.7,
                    )
                )
            )
        ]
    )
    mock_openai_parse.return_value = mock_response

    rewriter = ContentRewriter(api_key="sk-test")
    result = await rewriter.rewrite(sample_html, is_ymyl=True)

    assert result.confidence_score == 0.7

    kwargs = mock_openai_parse.call_args.kwargs
    system_message = kwargs["messages"][0]["content"]
    assert "HIGH CAUTION MODE" in system_message
    assert "YMYL" in system_message


async def test_requires_manual_validation_triggers_conservative_mode(mock_openai_parse, sample_html):
    """Test that requires_manual_validation flag uses conservative mode."""
    mock_response = MockResponse(
        choices=[
            MockChoice(
                message=MockParsedMessage(
                    parsed=RewriteResult(
                        rewritten_html=sample_html,
                        changes_summary="No changes made",
                        confidence_score=0.9,
                    )
                )
            )
        ]
    )
    mock_openai_parse.return_value = mock_response

    rewriter = ContentRewriter(api_key="sk-test")
    await rewriter.rewrite(sample_html, requires_manual_validation=True)

    kwargs = mock_openai_parse.call_args.kwargs
    system_message = kwargs["messages"][0]["content"]
    assert "HIGH CAUTION MODE" in system_message


async def test_structured_output_parsing(mock_openai_parse, sample_html):
    """Test that structured output is properly parsed."""
    expected_html = "<html><body><h1>New H1</h1></body></html>"
    mock_response = MockResponse(
        choices=[
            MockChoice(
                message=MockParsedMessage(
                    parsed=RewriteResult(
                        rewritten_html=expected_html,
                        changes_summary="Added H1",
                        confidence_score=0.95,
                    )
                )
            )
        ]
    )
    mock_openai_parse.return_value = mock_response

    rewriter = ContentRewriter(api_key="sk-test")
    result = await rewriter.rewrite(sample_html)

    assert isinstance(result, RewriteResult)
    assert result.rewritten_html == expected_html
    assert 0.0 <= result.confidence_score <= 1.0


async def test_empty_content_raises_error(mock_openai_parse):
    """Test that empty content raises RewriteError."""
    rewriter = ContentRewriter(api_key="sk-test")

    with pytest.raises(RewriteError, match="empty after sanitization"):
        await rewriter.rewrite("   ")


async def test_missing_api_key_raises_error():
    """Test that missing API key raises ValueError."""
    with patch("app.services.llm.rewriter.settings") as mock_settings:
        mock_settings.OPENAI_API_KEY = None
        rewriter = ContentRewriter()

        with pytest.raises(ValueError, match="API key is missing"):
            await rewriter.rewrite("<html></html>")


async def test_content_truncation(mock_openai_parse):
    """Test that content is truncated when exceeding max chars."""
    large_content = "x" * 60000
    mock_response = MockResponse(
        choices=[
            MockChoice(
                message=MockParsedMessage(
                    parsed=RewriteResult(
                        rewritten_html="<p>truncated</p>",
                        changes_summary="Content was truncated",
                        confidence_score=0.5,
                    )
                )
            )
        ]
    )
    mock_openai_parse.return_value = mock_response

    rewriter = ContentRewriter(api_key="sk-test")
    result = await rewriter.rewrite(large_content, max_input_chars=50000)

    assert result.rewritten_html == "<p>truncated</p>"

    kwargs = mock_openai_parse.call_args.kwargs
    user_content = kwargs["messages"][1]["content"]
    assert len(user_content) < 60000


async def test_html_sanitization_removes_scripts(mock_openai_parse):
    """Test that script tags are removed from input."""
    unsafe_html = """
    <html>
    <script>alert('xss')</script>
    <body><p>Safe content</p></body>
    </html>
    """
    mock_response = MockResponse(
        choices=[
            MockChoice(
                message=MockParsedMessage(
                    parsed=RewriteResult(
                        rewritten_html="<html><body><p>Safe content</p></body></html>",
                        changes_summary="Sanitized",
                        confidence_score=0.8,
                    )
                )
            )
        ]
    )
    mock_openai_parse.return_value = mock_response

    rewriter = ContentRewriter(api_key="sk-test")
    await rewriter.rewrite(unsafe_html)

    kwargs = mock_openai_parse.call_args.kwargs
    user_content = kwargs["messages"][1]["content"]
    assert "script" not in user_content.lower() or "alert" not in user_content


async def test_retry_on_rate_limit(mock_openai_parse, sample_html):
    """Test that the rewriter retries on rate limit errors."""
    mock_response = MockResponse(
        choices=[
            MockChoice(
                message=MockParsedMessage(
                    parsed=RewriteResult(
                        rewritten_html="<p>success</p>",
                        changes_summary="Done",
                        confidence_score=0.9,
                    )
                )
            )
        ]
    )

    mock_openai_parse.side_effect = [
        openai.RateLimitError(
            message="Rate limit reached",
            response=httpx.Response(status_code=429, request=httpx.Request("POST", "uri")),
            body=None
        ),
        mock_response
    ]

    rewriter = ContentRewriter(api_key="sk-test")
    from tenacity import wait_none
    rewriter.rewrite.retry.wait = wait_none()

    result = await rewriter.rewrite(sample_html)

    assert result.rewritten_html == "<p>success</p>"
    assert mock_openai_parse.call_count == 2


async def test_circuit_breaker_blocks_requests_when_open(mock_openai_parse, sample_html, fresh_circuit_breaker):
    """Test that circuit breaker blocks requests when open."""
    fresh_circuit_breaker._state.is_open = True
    fresh_circuit_breaker._state.opened_at = datetime.now(timezone.utc).timestamp()

    with patch("app.services.llm.rewriter.llm_circuit_breaker", fresh_circuit_breaker):
        rewriter = ContentRewriter(api_key="sk-test")

        with pytest.raises(RewriteError, match="Circuit breaker is open"):
            await rewriter.rewrite(sample_html)


def test_circuit_breaker_opens_after_threshold(fresh_circuit_breaker):
    """Test that circuit breaker opens after reaching failure threshold."""
    assert fresh_circuit_breaker.is_open() is False

    fresh_circuit_breaker.record_failure()
    assert fresh_circuit_breaker.is_open() is False

    fresh_circuit_breaker.record_failure()
    fresh_circuit_breaker.record_failure()

    assert fresh_circuit_breaker.is_open() is True


def test_circuit_breaker_resets_on_success(fresh_circuit_breaker):
    """Test that circuit breaker resets failure count on success."""
    fresh_circuit_breaker.record_failure()
    fresh_circuit_breaker.record_failure()

    assert fresh_circuit_breaker.failure_count == 2

    fresh_circuit_breaker.record_success()

    assert fresh_circuit_breaker.failure_count == 0


def test_circuit_breaker_prunes_old_failures(fresh_circuit_breaker):
    """Test that old failures are pruned from the window."""
    import time

    fresh_circuit_breaker._state.failure_times = [time.time() - 200]

    fresh_circuit_breaker._prune_old_failures(time.time())

    assert len(fresh_circuit_breaker._state.failure_times) == 0


async def test_empty_rewritten_html_raises_error(mock_openai_parse, sample_html):
    """Test that empty rewritten HTML raises RewriteError."""
    mock_response = MockResponse(
        choices=[
            MockChoice(
                message=MockParsedMessage(
                    parsed=RewriteResult(
                        rewritten_html="",
                        changes_summary="No changes",
                        confidence_score=0.5,
                    )
                )
            )
        ]
    )
    mock_openai_parse.return_value = mock_response

    rewriter = ContentRewriter(api_key="sk-test")

    with pytest.raises(RewriteError, match="Rewritten HTML is empty"):
        await rewriter.rewrite(sample_html)


def test_hallucination_zero_prompt_constraints():
    """Test that the Hallucination Zero prompt contains required constraints."""
    assert "NOT add any facts" in HALLUCINATION_ZERO_PROMPT
    assert "Preserve ALL numbers" in HALLUCINATION_ZERO_PROMPT
    assert "DOM" in HALLUCINATION_ZERO_PROMPT
    assert "page builders" in HALLUCINATION_ZERO_PROMPT.lower()


def test_ymyl_prompt_constraints():
    """Test that the YMYL conservative prompt is more restrictive."""
    assert "HIGH CAUTION MODE" in YMYL_CONSERVATIVE_PROMPT
    assert "ONLY fix obvious structural" in YMYL_CONSERVATIVE_PROMPT
    assert "Do NOT rephrase" in YMYL_CONSERVATIVE_PROMPT
    assert "human review" in YMYL_CONSERVATIVE_PROMPT.lower()
