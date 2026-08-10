"""Full Site Crawler Service.

Crawls an entire website starting from its root URL, respecting:
- robots.txt rules (SEO-v2-Bot user-agent)
- Rate limiting: 1 request/second per domain (NFR-13)
- Concurrency limits
- Hard cap quota to prevent budget leaks (NFR-12)
- Exponential backoff on 429/503 errors

Designed to run inside a Celery worker context (Story 3.4).
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ── Crawler Configuration ──────────────────────────────────────────
USER_AGENT = "SEO-v2-Bot"
DEFAULT_DOWNLOAD_DELAY: float = 1.0  # NFR-13: 1 req/sec per domain
DEFAULT_TIMEOUT: float = 15.0
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_CONCURRENT_REQUESTS: int = 1  # 1 concurrent for politeness
RETRY_HTTP_CODES: set[int] = {429, 503}
BACKOFF_BASE: float = 2.0  # Exponential backoff base
DEFAULT_HARD_CAP: int = 500  # Default page quota if none provided


@dataclass
class CrawlConfig:
    """Configuration for a full crawl session.

    Attributes:
        site_id: Database identifier for the site being crawled.
        start_url: Root URL to begin crawling from.
        hard_cap: Maximum number of pages to crawl (plan quota).
        download_delay: Seconds to wait between requests.
        timeout: HTTP request timeout in seconds.
        max_retries: Maximum retry attempts for transient errors.
        concurrent_requests: Number of concurrent requests allowed.
    """

    site_id: str
    start_url: str
    hard_cap: int = DEFAULT_HARD_CAP
    download_delay: float = DEFAULT_DOWNLOAD_DELAY
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    concurrent_requests: int = DEFAULT_CONCURRENT_REQUESTS


@dataclass
class PageResult:
    """Metadata extracted from a single crawled page.

    Attributes:
        url: Canonical URL of the page.
        status_code: HTTP response status code.
        title: Content of the <title> tag (if found).
        h1_count: Number of <h1> tags on the page.
        html_size: Size of the raw HTML response in bytes.
        crawled_at: Timestamp when the page was crawled.
        internal_links: List of internal link URLs discovered on the page.
    """

    url: str
    status_code: int
    title: Optional[str] = None
    h1_count: int = 0
    html_size: int = 0
    raw_html: Optional[str] = None
    crawled_at: Optional[datetime] = None
    internal_links: list[str] = field(default_factory=list)


@dataclass
class CrawlResult:
    """Summary result of a full crawl session.

    Attributes:
        site_id: Database identifier for the crawled site.
        pages: List of successfully crawled page results.
        total_discovered: Total number of unique URLs discovered.
        total_crawled: Number of pages actually fetched.
        quota_reached: Whether the hard cap was hit.
        errors: List of error descriptions encountered.
        started_at: Crawl start timestamp.
        finished_at: Crawl finish timestamp.
    """

    site_id: str
    pages: list[PageResult] = field(default_factory=list)
    total_discovered: int = 0
    total_crawled: int = 0
    quota_reached: bool = False
    errors: list[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    sector: Optional[str] = None
    is_ymyl: bool = False


class QuotaExceededError(Exception):
    """Raised when the crawl hard cap (page quota) is reached."""

    pass


class FullCrawler:
    """Async full-site crawler with politeness and safety controls.

    Usage::

        config = CrawlConfig(site_id="abc", start_url="https://example.com")
        crawler = FullCrawler(config)
        result = await crawler.crawl()
    """

    def __init__(
        self,
        config: CrawlConfig,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        self.config = config
        self._visited: set[str] = set()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._robot_parser: Optional[RobotFileParser] = None
        self._robots_loaded: bool = False
        self._domain: str = ""
        self._scheme: str = ""
        self._result: CrawlResult = CrawlResult(site_id=config.site_id)
        self._semaphore = asyncio.Semaphore(config.concurrent_requests)
        self._last_request_time: float = 0.0
        self._progress_callback = progress_callback
        self._extracted_text_for_classification: list[str] = []

    async def crawl(self) -> CrawlResult:
        """Execute the full site crawl.

        Returns:
            CrawlResult with all discovered pages and metadata.
        """
        self._result.started_at = datetime.now(timezone.utc)
        parsed = urlparse(self.config.start_url)
        self._domain = parsed.netloc
        self._scheme = parsed.scheme

        log.info(
            "Starting full crawl for site %s at %s (hard_cap=%d)",
            self.config.site_id,
            self.config.start_url,
            self.config.hard_cap,
        )

        # Load robots.txt before crawling
        await self._load_robots_txt()

        # Seed the queue with the start URL
        normalized_start = self._normalize_url(self.config.start_url)
        self._queue.put_nowait(normalized_start)
        self._visited.add(normalized_start)
        self._result.total_discovered = 1

        async with httpx.AsyncClient(
            timeout=self.config.timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            try:
                while not self._queue.empty():
                    url = self._queue.get_nowait()
                    await self._process_url(client, url)
            except QuotaExceededError:
                self._result.quota_reached = True
                log.warning(
                    "Hard cap reached (%d pages) for site %s — stopping crawl",
                    self.config.hard_cap,
                    self.config.site_id,
                )

        # Classify the accumulated page text.
        if self._extracted_text_for_classification:
            try:
                from app.services.llm.classifier import ClassificationAgent

                agent = ClassificationAgent()
                combined_text = "\n\n".join(self._extracted_text_for_classification)
                classification = await agent.classify_site(combined_text)
                self._result.sector = classification.sector
                self._result.is_ymyl = classification.is_ymyl
                log.info(
                    f"Classification completed for {self.config.site_id}: sector={classification.sector}, is_ymyl={classification.is_ymyl}"
                )
            except Exception as e:
                log.error(f"Failed to run Classification Agent: {e}")
                self._result.errors.append(f"Classification failed: {e}")

        self._result.finished_at = datetime.now(timezone.utc)
        log.info(
            "Crawl complete for site %s: %d pages crawled, %d discovered, quota_reached=%s",
            self.config.site_id,
            self._result.total_crawled,
            self._result.total_discovered,
            self._result.quota_reached,
        )
        return self._result

    async def _load_robots_txt(self) -> None:
        """Fetch and parse robots.txt from the target domain."""
        robots_url = f"{self._scheme}://{self._domain}/robots.txt"
        self._robot_parser = RobotFileParser()

        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                resp = await client.get(robots_url, headers={"User-Agent": USER_AGENT})
                if resp.status_code == 200:
                    self._robot_parser.parse(resp.text.splitlines())
                    self._robots_loaded = True
                    log.info("robots.txt loaded from %s", robots_url)
                else:
                    # No robots.txt → everything allowed
                    self._robots_loaded = False
                    log.info(
                        "No robots.txt found at %s (status %d) — assuming all allowed",
                        robots_url,
                        resp.status_code,
                    )
        except Exception as exc:
            self._robots_loaded = False
            log.warning("Could not fetch robots.txt from %s: %s", robots_url, exc)

    def _can_fetch(self, url: str) -> bool:
        """Check whether the crawler is allowed to fetch a URL per robots.txt."""
        if not self._robots_loaded or self._robot_parser is None:
            return True
        return self._robot_parser.can_fetch(USER_AGENT, url)

    async def _process_url(self, client: httpx.AsyncClient, url: str) -> None:
        """Fetch a URL, parse its content, and enqueue discovered links."""
        # Check quota BEFORE fetching
        if self._result.total_crawled >= self.config.hard_cap:
            raise QuotaExceededError(
                f"Hard cap of {self.config.hard_cap} pages reached"
            )

        # Check robots.txt
        if not self._can_fetch(url):
            log.debug("Blocked by robots.txt: %s", url)
            return

        # Enforce download delay (rate limiting)
        await self._enforce_delay()

        # Fetch with retry
        page_result = await self._fetch_with_retry(client, url)
        if page_result is None:
            return

        self._result.pages.append(page_result)
        self._result.total_crawled += 1

        if self._progress_callback:
            self._progress_callback(
                self._result.total_crawled, self._result.total_discovered
            )

        # Enqueue discovered internal links
        for link in page_result.internal_links:
            normalized = self._normalize_url(link)
            if normalized not in self._visited:
                # Check quota before adding to queue
                if self._result.total_discovered >= self.config.hard_cap:
                    self._result.quota_reached = True
                    log.info(
                        "Discovery cap reached (%d) — not adding more URLs",
                        self.config.hard_cap,
                    )
                    break
                self._visited.add(normalized)
                self._result.total_discovered += 1
                self._queue.put_nowait(normalized)

    async def _enforce_delay(self) -> None:
        """Wait to respect the download delay (rate limiting)."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.config.download_delay:
            await asyncio.sleep(self.config.download_delay - elapsed)
        self._last_request_time = time.monotonic()

    async def _fetch_with_retry(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[PageResult]:
        """Fetch a URL with exponential backoff on retryable HTTP codes.

        Args:
            client: Shared httpx async client.
            url: URL to fetch.

        Returns:
            PageResult on success, None on permanent failure.
        """
        for attempt in range(self.config.max_retries + 1):
            try:
                async with self._semaphore:
                    resp = await client.get(url)

                if resp.status_code in RETRY_HTTP_CODES:
                    if attempt < self.config.max_retries:
                        wait_time = BACKOFF_BASE**attempt
                        log.warning(
                            "Received %d for %s — retrying in %.1fs (attempt %d/%d)",
                            resp.status_code,
                            url,
                            wait_time,
                            attempt + 1,
                            self.config.max_retries,
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        error_msg = f"Exhausted retries for {url} with status {resp.status_code}"
                        self._result.errors.append(error_msg)
                        log.error(error_msg)
                        return None

                return self._parse_response(url, resp)

            except httpx.TimeoutException:
                if attempt < self.config.max_retries:
                    wait_time = BACKOFF_BASE**attempt
                    log.warning(
                        "Timeout for %s — retrying in %.1fs (attempt %d/%d)",
                        url,
                        wait_time,
                        attempt + 1,
                        self.config.max_retries,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    self._result.errors.append(
                        f"Timeout after {self.config.max_retries} retries: {url}"
                    )
                    log.error("Timeout after all retries for %s", url)
            except Exception as exc:
                self._result.errors.append(f"Error fetching {url}: {exc}")
                log.error("Unexpected error fetching %s: %s", url, exc)
                break

        return None

    def _parse_response(self, url: str, resp: httpx.Response) -> PageResult:
        """Parse an HTTP response and extract page metadata + internal links.

        Args:
            url: Original requested URL.
            resp: httpx Response object.

        Returns:
            PageResult with extracted metadata.
        """
        html = resp.text
        content_type = resp.headers.get("content-type", "")

        page = PageResult(
            url=str(resp.url),
            status_code=resp.status_code,
            html_size=len(html.encode("utf-8", errors="replace")),
            crawled_at=datetime.now(timezone.utc),
        )

        # Only parse HTML responses
        if "text/html" not in content_type:
            return page

        page.raw_html = html

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Extract title
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            page.title = title_tag.string.strip()

        # Count h1 tags
        page.h1_count = len(soup.find_all("h1"))

        # Extract internal links
        page.internal_links = self._extract_internal_links(soup, str(resp.url))

        # Check if this is a homepage or about page for classification
        parsed_url = urlparse(url)
        path = parsed_url.path.lower().strip("/")
        if not path or "about" in path or "propos" in path or "entreprise" in path:
            text_content = soup.get_text(separator=" ", strip=True)
            self._extracted_text_for_classification.append(text_content[:5000])

        return page

    def _extract_internal_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """Extract all internal HTML links from a parsed page.

        Filters out:
        - External links (different domain)
        - Non-HTTP schemes (mailto:, tel:, javascript:)
        - Anchors (#) only links
        - Non-HTML resources (images, PDFs, etc.)

        Args:
            soup: Parsed BeautifulSoup object.
            base_url: Base URL for resolving relative links.

        Returns:
            List of absolute internal URLs.
        """
        links: list[str] = []
        non_html_extensions = {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".svg",
            ".webp",
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".zip",
            ".tar",
            ".gz",
            ".rar",
            ".mp3",
            ".mp4",
            ".avi",
            ".mov",
            ".css",
            ".js",
            ".json",
            ".xml",
            ".ico",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
        }

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()

            # Skip empty, anchor-only, and non-HTTP schemes
            if not href or href.startswith("#"):
                continue
            if href.startswith(("mailto:", "tel:", "javascript:")):
                continue

            # Resolve relative URLs
            absolute_url = urljoin(base_url, href)
            parsed = urlparse(absolute_url)

            # Must be same domain
            if parsed.netloc != self._domain:
                continue

            # Must be http(s)
            if parsed.scheme not in ("http", "https"):
                continue

            # Filter non-HTML extensions
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in non_html_extensions):
                continue

            # Remove fragment, keep clean URL
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean_url += f"?{parsed.query}"

            links.append(clean_url)

        return links

    def _normalize_url(self, url: str) -> str:
        """Normalize a URL for deduplication.

        Strips fragments and trailing slashes for consistent comparison.

        Args:
            url: URL to normalize.

        Returns:
            Normalized URL string.
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip("/") or "/"
        normalized = f"{parsed.scheme}://{parsed.netloc}{path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        return normalized
