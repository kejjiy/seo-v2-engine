import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_limiter import FastAPILimiter

from app.api.v1.endpoints import audit, keys, reports, scan, sync, veto
from app.core.exceptions import RateLimitExceeded

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    await FastAPILimiter.init(r)
    yield
    await r.close()


app = FastAPI(
    title="SEO-v2 Engine",
    description="Backend API for SEO-v2 content optimization platform",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "type": "about:blank",
            "title": "Too Many Requests",
            "status": 429,
            "detail": exc.detail,
            "instance": request.url.path,
        },
        media_type="application/problem+json",
    )


app.include_router(scan.router, prefix="/api/v1", tags=["scan"])
app.include_router(keys.router, prefix="/api/v1", tags=["keys"])
app.include_router(sync.router, prefix="/api/v1", tags=["sync"])
app.include_router(veto.router, prefix="/api/v1", tags=["veto"])
app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
app.include_router(audit.router, prefix="/api/v1", tags=["audit"])


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    return {"status": "healthy"}
