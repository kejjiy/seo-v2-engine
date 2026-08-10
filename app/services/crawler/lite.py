import httpx
import logging
from urllib.robotparser import RobotFileParser
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)

USER_AGENT = "SEO-v2-Bot"
TIMEOUT = 10.0

async def can_fetch(url: str) -> bool:
    """
    Check robots.txt for the given URL.
    """
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    robots_url = urljoin(base_url, "/robots.txt")

    rp = RobotFileParser()
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            response = await client.get(robots_url)
            if response.status_code == 200:
                rp.parse(response.text.splitlines())
            else:
                # If robots.txt doesn't exist, we assume it's allowed
                return True
    except Exception as e:
        log.warning(f"Could not fetch robots.txt from {robots_url}: {e}")
        return True

    return rp.can_fetch(USER_AGENT, url)

async def fetch_page(url: str) -> dict:
    """
    Fetch a single page's HTML content.

    Args:
        url: The URL to fetch.

    Returns:
        dict: Contains 'html', 'status_code', and 'url'.

    Raises:
        ValueError: If robots.txt disallows fetching.
        httpx.TimeoutException: If the request times out.
    """
    if not await can_fetch(url):
        raise ValueError(f"Robots.txt disallowed for URL: {url}")

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, verify=False) as client:
        headers = {"User-Agent": USER_AGENT}
        response = await client.get(url, headers=headers)

        return {
            "html": response.text,
            "status_code": response.status_code,
            "url": str(response.url)
        }
