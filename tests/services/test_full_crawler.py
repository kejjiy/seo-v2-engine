"""Tests for the Full Site Crawler (Story 3.4).

Covers:
- Task 1: Crawler initialization, robots.txt, rate limiting, backoff
- Task 2: Link extraction, metadata parsing, non-HTML filtering
- Task 3: Hard cap quota enforcement
- Task 4: Celery task integration (separate test for worker)
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import httpx
import pytest
from bs4 import BeautifulSoup

from app.services.crawler.full import (
    BACKOFF_BASE,
    DEFAULT_HARD_CAP,
    USER_AGENT,
    CrawlConfig,
    CrawlResult,
    FullCrawler,
    PageResult,
    QuotaExceededError,
)


# ── Helpers ─────────────────────────────────────────────────────────
def _make_response(
    status_code: int = 200,
    text: str = "<html><head><title>Test</title></head><body><h1>Hello</h1></body></html>",
    url: str = "https://example.com/",
    content_type: str = "text/html; charset=utf-8",
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.url = url
    resp.headers = {"content-type": content_type}
    return resp


def _config(**overrides) -> CrawlConfig:
    """Create a CrawlConfig with sensible defaults and optional overrides."""
    defaults = {
        "site_id": "test-site-001",
        "start_url": "https://example.com",
        "hard_cap": 10,
        "download_delay": 0.0,  # No delay in tests
        "timeout": 5.0,
        "max_retries": 2,
        "concurrent_requests": 1,
    }
    defaults.update(overrides)
    return CrawlConfig(**defaults)


# ── Task 1: Crawler Initialization & Configuration ──────────────────


class TestCrawlerInit:
    """Test crawler initialization, config, and robots.txt handling."""

    def test_config_defaults(self):
        """CrawlConfig should have sensible defaults for all settings."""
        config = CrawlConfig(site_id="s1", start_url="https://example.com")
        assert config.hard_cap == DEFAULT_HARD_CAP
        assert config.download_delay == 1.0
        assert config.max_retries == 3
        assert config.concurrent_requests == 1

    def test_config_custom_values(self):
        """CrawlConfig should accept and preserve custom values."""
        config = _config(hard_cap=50, download_delay=2.0)
        assert config.hard_cap == 50
        assert config.download_delay == 2.0

    def test_crawler_user_agent(self):
        """Crawler user-agent must be strictly 'SEO-v2-Bot'."""
        assert USER_AGENT == "SEO-v2-Bot"

    @pytest.mark.asyncio
    async def test_robots_txt_loaded(self):
        """Crawler should load and respect robots.txt on init."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"
        crawler._scheme = "https"

        robots_text = "User-agent: SEO-v2-Bot\nDisallow: /admin\nAllow: /"
        mock_resp = MagicMock(status_code=200, text=robots_text)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await crawler._load_robots_txt()

        assert crawler._robots_loaded is True
        assert crawler._can_fetch("https://example.com/page") is True
        assert crawler._can_fetch("https://example.com/admin") is False

    @pytest.mark.asyncio
    async def test_robots_txt_missing(self):
        """When robots.txt is absent (404), everything should be allowed."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"
        crawler._scheme = "https"

        mock_resp = MagicMock(status_code=404, text="Not Found")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await crawler._load_robots_txt()

        assert crawler._robots_loaded is False
        assert crawler._can_fetch("https://example.com/anything") is True

    @pytest.mark.asyncio
    async def test_robots_txt_network_error(self):
        """When robots.txt fetch fails, assume everything allowed."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"
        crawler._scheme = "https"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await crawler._load_robots_txt()

        assert crawler._robots_loaded is False

    @pytest.mark.asyncio
    async def test_exponential_backoff_on_429(self):
        """Crawler should retry with exponential backoff on 429 responses."""
        config = _config(max_retries=2)
        crawler = FullCrawler(config)
        crawler._domain = "example.com"
        crawler._scheme = "https"

        resp_429 = _make_response(status_code=429, text="Too Many Requests")
        resp_200 = _make_response(status_code=200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[resp_429, resp_200])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await crawler._fetch_with_retry(
                mock_client, "https://example.com/page"
            )

        assert result is not None
        assert result.status_code == 200
        # Should have slept once with backoff
        mock_sleep.assert_called_once_with(BACKOFF_BASE**0)  # 2^0 = 1.0

    @pytest.mark.asyncio
    async def test_exponential_backoff_on_503(self):
        """Crawler should retry with exponential backoff on 503 responses."""
        config = _config(max_retries=2)
        crawler = FullCrawler(config)
        crawler._domain = "example.com"
        crawler._scheme = "https"

        resp_503 = _make_response(status_code=503, text="Service Unavailable")
        resp_200 = _make_response(status_code=200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[resp_503, resp_200])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await crawler._fetch_with_retry(
                mock_client, "https://example.com/page"
            )

        assert result is not None
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_retry_exhausted_returns_none(self):
        """When all retries are exhausted, _fetch_with_retry returns None."""
        config = _config(max_retries=1)
        crawler = FullCrawler(config)
        crawler._domain = "example.com"
        crawler._scheme = "https"

        resp_429 = _make_response(status_code=429, text="Too Many Requests")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp_429)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await crawler._fetch_with_retry(
                mock_client, "https://example.com/page"
            )

        # After max_retries retries, it should return None to avoid polluting the DB
        assert result is None
        assert any(crawler._result.errors)

    @pytest.mark.asyncio
    async def test_timeout_retry_exhausted(self):
        """On persistent timeouts, errors should be recorded."""
        config = _config(max_retries=1)
        crawler = FullCrawler(config)
        crawler._domain = "example.com"
        crawler._scheme = "https"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timed out"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await crawler._fetch_with_retry(
                mock_client, "https://example.com/page"
            )

        assert result is None
        assert len(crawler._result.errors) == 1
        assert "Timeout" in crawler._result.errors[0]


# ── Task 2: Page Discovery and Parsing ──────────────────────────────


class TestPageParsing:
    """Test link extraction, metadata parsing, and non-HTML filtering."""

    def test_extract_internal_links_basic(self):
        """Should extract internal links from anchor tags."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"

        html = """
        <html><body>
            <a href="/page1">Page 1</a>
            <a href="/page2">Page 2</a>
            <a href="https://example.com/page3">Page 3</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = crawler._extract_internal_links(soup, "https://example.com/")

        assert "https://example.com/page1" in links
        assert "https://example.com/page2" in links
        assert "https://example.com/page3" in links

    def test_filter_external_links(self):
        """External links should be excluded."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"

        html = """
        <html><body>
            <a href="https://example.com/internal">Internal</a>
            <a href="https://other-site.com/page">External</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = crawler._extract_internal_links(soup, "https://example.com/")

        assert "https://example.com/internal" in links
        assert not any("other-site.com" in l for l in links)

    def test_filter_non_html_resources(self):
        """Non-HTML resources (images, PDFs, CSS) should be filtered out."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"

        html = """
        <html><body>
            <a href="/page">Page</a>
            <a href="/image.jpg">Image</a>
            <a href="/doc.pdf">PDF</a>
            <a href="/style.css">CSS</a>
            <a href="/script.js">JS</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = crawler._extract_internal_links(soup, "https://example.com/")

        assert "https://example.com/page" in links
        assert not any(l.endswith(".jpg") for l in links)
        assert not any(l.endswith(".pdf") for l in links)
        assert not any(l.endswith(".css") for l in links)
        assert not any(l.endswith(".js") for l in links)

    def test_filter_mailto_tel_javascript(self):
        """mailto:, tel:, and javascript: links should be skipped."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"

        html = """
        <html><body>
            <a href="mailto:test@example.com">Email</a>
            <a href="tel:+1234567890">Phone</a>
            <a href="javascript:void(0)">JS</a>
            <a href="/real-page">Real</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = crawler._extract_internal_links(soup, "https://example.com/")

        assert len(links) == 1
        assert "https://example.com/real-page" in links

    def test_filter_anchor_only_links(self):
        """Fragment-only links (#section) should be skipped."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"

        html = """
        <html><body>
            <a href="#top">Anchor</a>
            <a href="/page#section">Page with anchor</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = crawler._extract_internal_links(soup, "https://example.com/")

        # #top should be filtered, /page#section should be included (without fragment)
        assert len(links) == 1
        assert "https://example.com/page" in links

    def test_parse_metadata_title_h1(self):
        """Should extract title and h1 count from HTML response."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"

        html = "<html><head><title>My Title</title></head><body><h1>Heading 1</h1><h1>Heading 2</h1></body></html>"
        resp = _make_response(text=html)
        page = crawler._parse_response("https://example.com/", resp)

        assert page.title == "My Title"
        assert page.h1_count == 2
        assert page.html_size > 0
        assert page.raw_html == html

    def test_parse_non_html_response(self):
        """Non-HTML content-type should still return PageResult but no parsed data."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"

        resp = _make_response(
            text='{"key": "value"}',
            content_type="application/json",
        )
        page = crawler._parse_response("https://example.com/api", resp)

        assert page.title is None
        assert page.h1_count == 0
        assert page.internal_links == []

    def test_parse_html_without_title(self):
        """Pages without a <title> tag should have title=None."""
        config = _config()
        crawler = FullCrawler(config)
        crawler._domain = "example.com"

        html = "<html><body><h1>Hello</h1></body></html>"
        resp = _make_response(text=html)
        page = crawler._parse_response("https://example.com/", resp)

        assert page.title is None


# ── Task 3: Database Integration & Quota (Hard Cap) ─────────────────


class TestQuotaEnforcement:
    """Test hard cap quota enforcement during crawl."""

    @pytest.mark.asyncio
    async def test_hard_cap_stops_crawl(self):
        """Crawl should stop when hard_cap is reached."""
        config = _config(hard_cap=2)
        crawler = FullCrawler(config)

        # Create pages that link to each other, creating a bigger graph than the cap
        page1_html = """<html><head><title>Page 1</title></head><body>
            <h1>P1</h1>
            <a href="/page2">2</a>
            <a href="/page3">3</a>
            <a href="/page4">4</a>
        </body></html>"""

        page2_html = """<html><head><title>Page 2</title></head><body>
            <h1>P2</h1>
            <a href="/page5">5</a>
        </body></html>"""

        responses = {
            "https://example.com/": _make_response(
                text=page1_html, url="https://example.com/"
            ),
            "https://example.com/page2": _make_response(
                text=page2_html, url="https://example.com/page2"
            ),
            "https://example.com/page3": _make_response(
                text="<html><body><h1>P3</h1></body></html>",
                url="https://example.com/page3",
            ),
            "https://example.com/page4": _make_response(
                text="<html><body><h1>P4</h1></body></html>",
                url="https://example.com/page4",
            ),
            "https://example.com/page5": _make_response(
                text="<html><body><h1>P5</h1></body></html>",
                url="https://example.com/page5",
            ),
        }

        # Mock robots.txt as not found
        robots_resp = MagicMock(status_code=404, text="Not Found")

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "robots.txt" in url_str:
                return robots_resp
            # Normalize URL for lookup
            for key in responses:
                if url_str.rstrip("/") == key.rstrip("/") or url_str == key:
                    return responses[key]
            return _make_response(status_code=404, text="Not Found", url=url_str)

        with patch("app.services.crawler.full.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await crawler.crawl()

        assert result.quota_reached is True
        assert result.total_crawled <= 2

    @pytest.mark.asyncio
    async def test_crawl_within_quota(self):
        """Crawl should complete normally when within quota."""
        config = _config(hard_cap=10)
        crawler = FullCrawler(config)

        # Simple site: root page with 2 links
        root_html = """<html><head><title>Root</title></head><body>
            <h1>Root</h1>
            <a href="/about">About</a>
            <a href="/contact">Contact</a>
        </body></html>"""

        about_html = (
            "<html><head><title>About</title></head><body><h1>About</h1></body></html>"
        )
        contact_html = "<html><head><title>Contact</title></head><body><h1>Contact</h1></body></html>"

        responses = {
            "https://example.com/": _make_response(
                text=root_html, url="https://example.com/"
            ),
            "https://example.com/about": _make_response(
                text=about_html, url="https://example.com/about"
            ),
            "https://example.com/contact": _make_response(
                text=contact_html, url="https://example.com/contact"
            ),
        }

        robots_resp = MagicMock(status_code=404, text="Not Found")

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "robots.txt" in url_str:
                return robots_resp
            for key in responses:
                if url_str.rstrip("/") == key.rstrip("/") or url_str == key:
                    return responses[key]
            return _make_response(status_code=404, text="Not Found", url=url_str)

        with patch("app.services.crawler.full.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await crawler.crawl()

        assert result.quota_reached is False
        assert result.total_crawled == 3
        assert result.total_discovered == 3

        titles = {p.title for p in result.pages}
        assert "Root" in titles
        assert "About" in titles
        assert "Contact" in titles


# ── URL Normalization ───────────────────────────────────────────────


class TestURLNormalization:
    """Test URL normalization for deduplication."""

    def test_normalize_strips_trailing_slash(self):
        config = _config()
        crawler = FullCrawler(config)

        assert (
            crawler._normalize_url("https://example.com/page/")
            == "https://example.com/page"
        )
        assert (
            crawler._normalize_url("https://example.com/page")
            == "https://example.com/page"
        )

    def test_normalize_strips_fragment(self):
        config = _config()
        crawler = FullCrawler(config)

        assert (
            crawler._normalize_url("https://example.com/page#section")
            == "https://example.com/page"
        )

    def test_normalize_preserves_query(self):
        config = _config()
        crawler = FullCrawler(config)

        assert (
            crawler._normalize_url("https://example.com/page?q=1")
            == "https://example.com/page?q=1"
        )

    def test_normalize_root_url(self):
        config = _config()
        crawler = FullCrawler(config)

        assert crawler._normalize_url("https://example.com") == "https://example.com/"
        assert crawler._normalize_url("https://example.com/") == "https://example.com/"


# ── CrawlResult Dataclass ──────────────────────────────────────────


class TestCrawlResult:
    """Test CrawlResult data integrity."""

    def test_default_values(self):
        result = CrawlResult(site_id="test")
        assert result.site_id == "test"
        assert result.pages == []
        assert result.total_crawled == 0
        assert result.total_discovered == 0
        assert result.quota_reached is False
        assert result.errors == []

    def test_page_result_defaults(self):
        page = PageResult(url="https://example.com", status_code=200)
        assert page.title is None
        assert page.h1_count == 0
        assert page.html_size == 0
        assert page.internal_links == []
