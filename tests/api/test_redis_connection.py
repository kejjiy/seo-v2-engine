import pytest
import redis.asyncio as redis
import os

@pytest.mark.asyncio
async def test_redis_connection():
    redis_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    # Ensure we are using the correct redis host if running inside docker or locally
    # For local testing, it might be localhost if port forwarded, or we might need to mock it.
    # But here we want to test actual connection if possible.

    r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    try:
        response = await r.ping()
        assert response is True
    finally:
        await r.close()
