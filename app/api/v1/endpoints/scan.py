from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from fastapi_limiter.depends import RateLimiter
from app.core.exceptions import RateLimitExceeded

router = APIRouter()

class ScanRequest(BaseModel):
    url: str

async def rate_limit_callback(request: Request, response: Response, pexpire: int):
    raise RateLimitExceeded(detail=f"Rate limit exceeded. Try again in {pexpire} seconds.")

from app.services.crawler.lite import fetch_page
from fastapi import HTTPException
from app.services.ims_calculator import calculate_ims

@router.post("/scan", dependencies=[Depends(RateLimiter(times=5, seconds=60, callback=rate_limit_callback))])
async def scan_url(request: ScanRequest):
    try:
        page_data = await fetch_page(request.url)
        html = page_data["html"]
    except Exception as e:
        import traceback
        err_trace = traceback.format_exc()
        print("Crawler Error:", err_trace)
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}. Trace: {err_trace}")

    # Calculate real IMS score and frictions
    ims_result = calculate_ims(html)

    # Map raw string friction points from calculate_ims to severity objects
    friction_points = []
    for fp in ims_result.friction_points:
        severity = "medium"
        if "Missing H1" in fp or "Empty content" in fp:
            severity = "high"
        elif "Low word count" in fp:
            severity = "medium"
        elif "Multiple" in fp or "Broken" in fp:
            severity = "low"

        friction_points.append({
            "message": fp,
            "severity": severity
        })

    return {
        "message": "Scan complete",
        "url": request.url,
        "score": ims_result.score,
        "friction_points": friction_points
    }
