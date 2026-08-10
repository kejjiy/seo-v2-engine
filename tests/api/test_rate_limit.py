import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
import redis.asyncio as redis
from app.main import app
from fastapi_limiter import FastAPILimiter

# --- Fake Redis Implementation ---

class FakeRedis:
    """
    A fake Redis client that stores data in memory.
    Designed to work with fastapi-limiter's Lua scripts.
    """
    def __init__(self):
        self._data = {}
        self._counters = {}
        self.encoding = "utf-8"
        self.decode_responses = True

    async def get(self, key):
        return self._data.get(key)

    async def set(self, key, value, ex=None, px=None, nx=False):
        self._data[key] = value
        return True

    async def incr(self, key):
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    async def expire(self, key, seconds):
        return True

    async def pttl(self, key):
        return 60000  # Always return 60s TTL for simplicity

    async def evalsha(self, sha, numkeys, *keys_and_args):
        """
        Simulate the rate limiting logic usually done by Lua script.
        """
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]

        key = keys[0] if keys else "default"
        # args[0] is limit, args[1] is expire
        limit = int(args[0]) if args else 1

        self._counters[key] = self._counters.get(key, 0) + 1
        current_count = self._counters[key]

        if current_count > limit:
            # Rate limited: return pttl (ms)
            return 60000

        # Allowed: return 0
        return 0

    async def script_load(self, script):
        return "fake_sha"

    async def close(self):
        pass

    async def ping(self):
        return True

    def pipeline(self, transaction=True):
        return self

    async def execute(self):
        return []

# --- Fixtures ---

@pytest.fixture
def fake_redis_instance():
    return FakeRedis()

@pytest_asyncio.fixture
async def client(fake_redis_instance):
    """
    Test client that uses a patched redis.from_url and manually initializes FastAPILimiter.
    """
    # Patch redis.asyncio.from_url to return our fake instance
    with patch("redis.asyncio.from_url", return_value=fake_redis_instance):
        # Manually initialize FastAPILimiter since app.router.lifespan_context isn't working reliably in tests
        await FastAPILimiter.init(fake_redis_instance)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        # Cleanup
        await fake_redis_instance.close()
        FastAPILimiter.redis = None

# --- Tests ---

@pytest.mark.asyncio
async def test_health_endpoint(client):
    """Test the health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

@pytest.mark.asyncio
async def test_rate_limit_scan_endpoint(client):
    """
    Test rate limiting:
    - 5 requests OK
    - 6th request 429
    """
    url = "/api/v1/scan"
    payload = {"url": "https://example.com"}

    # 1. Five allowed requests
    for i in range(5):
        response = await client.post(url, json=payload)
        assert response.status_code == 200, f"Request {i+1} failed: {response.text}"
        assert response.json()["message"] == "Scan started"

    # 2. Sixth request blocked
    response = await client.post(url, json=payload)
    assert response.status_code == 429, "Rate limit did not trigger on 6th request"

    # 3. Check Error Format (RFC 7807)
    data = response.json()
    assert data["status"] == 429
    assert data["title"] == "Too Many Requests"
    assert "detail" in data
    assert "Rate limit exceeded" in data["detail"]

@pytest.mark.asyncio
async def test_scan_endpoint_valid(client):
    """Test a single valid request."""
    response = await client.post("/api/v1/scan", json={"url": "https://valid.com"})
    assert response.status_code == 200
    assert response.json()["url"] == "https://valid.com"
