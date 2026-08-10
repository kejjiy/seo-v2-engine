import pytest
import httpx
from unittest.mock import patch, MagicMock
from app.services.crawler.lite import fetch_page
from app.core.exceptions import RateLimitExceeded # Wait, I might need a different exception for robots.txt or just return error

@pytest.mark.asyncio
async def test_fetch_page_success():
    url = "https://example.com"
    mock_html = "<html><body>Hello World</body></html>"

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            text=mock_html,
            is_success=True,
            url=url
        )

        # Mock the internal can_fetch call
        with patch("app.services.crawler.lite.can_fetch", return_value=True):
            result = await fetch_page(url)

            assert result["status_code"] == 200
            assert result["html"] == mock_html
            assert result["url"] == url

@pytest.mark.asyncio
async def test_fetch_page_robots_disallowed():
    url = "https://example.com/private"

    with patch("app.services.crawler.lite.can_fetch", return_value=False):
        with pytest.raises(ValueError, match="Robots.txt disallowed"):
            await fetch_page(url)

@pytest.mark.asyncio
async def test_fetch_page_timeout():
    url = "https://example.com"

    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("Timeout")):
        with patch("app.services.crawler.lite.can_fetch", return_value=True):
            with pytest.raises(httpx.TimeoutException):
                await fetch_page(url)

@pytest.mark.asyncio
async def test_can_fetch_allowed():
    url = "https://example.com/allowed"
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text="User-agent: *\nAllow: /")
        from app.services.crawler.lite import can_fetch
        assert await can_fetch(url) is True

@pytest.mark.asyncio
async def test_can_fetch_disallowed():
    url = "https://example.com/disallowed"
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text="User-agent: SEO-v2-Bot\nDisallow: /disallowed")
        from app.services.crawler.lite import can_fetch
        assert await can_fetch(url) is False

@pytest.mark.asyncio
async def test_fetch_page_http_error():
    url = "https://example.com/404"

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=404,
            text="Not Found",
            is_success=False
        )

        with patch("urllib.robotparser.RobotFileParser.can_fetch", return_value=True):
            result = await fetch_page(url)
            assert result["status_code"] == 404
            assert result["html"] == "Not Found"
