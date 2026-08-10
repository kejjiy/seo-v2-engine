"""PDF report generation endpoints."""

import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi_limiter.depends import RateLimiter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from io import BytesIO

from app.core.exceptions import RateLimitExceeded
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.organization import Organization
from app.models.site import Site
from app.services import audit_service
from app.services.reporting.aggregator import ReportAggregator
from app.services.reporting.pdf_generator import PDFGenerator, BrandingConfig


def validate_logo_url(url: Optional[str]) -> Optional[str]:
    """Validate logo URL to prevent SSRF attacks.

    Only HTTPS URLs are allowed. Blocks internal IPs and localhost.
    """
    if not url:
        return None

    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise ValueError("Logo URL must use HTTPS")

    hostname = parsed.hostname or ""
    blocked_patterns = [
        r"^localhost$",
        r"^127\.",
        r"^10\.",
        r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
        r"^192\.168\.",
        r"^0\.0\.0\.0$",
        r"^::1$",
        r"^\[::\]$",
    ]

    for pattern in blocked_patterns:
        if re.match(pattern, hostname, re.IGNORECASE):
            raise ValueError("Logo URL cannot point to internal addresses")

    return url


def validate_hex_color(color: Optional[str]) -> str:
    """Validate hex color format."""
    if not color:
        return "#059669"

    if not re.match(r"^#[0-9A-Fa-f]{6}$", color):
        raise ValueError(f"Invalid hex color format: {color}")

    return color


router = APIRouter()


class PDFReportQuery(BaseModel):
    period_days: int = Field(default=30, ge=1, le=90)


class HTMLPreviewResponse(BaseModel):
    html: str


async def rate_limit_callback(request: Request, response: Response, pexpire: int):
    raise RateLimitExceeded(
        detail=f"Rate limit exceeded. Try again in {pexpire} seconds."
    )


def _get_organization_for_site(db: Session, site_id: str) -> Optional[Organization]:
    """Get organization associated with a site."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        return None
    return (
        db.query(Organization).filter(Organization.id == site.organization_id).first()
    )


def _get_accessible_site_or_404(db: Session, site_id: str, current_user: dict) -> Site:
    site = audit_service.get_accessible_site(db, site_id, current_user.get("user_id"))
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.get(
    "/sites/{site_id}/report/pdf",
    responses={200: {"content": {"application/pdf": {}}}},
    dependencies=[
        Depends(RateLimiter(times=5, seconds=3600, callback=rate_limit_callback))
    ],
)
async def generate_pdf_report(
    site_id: str,
    period_days: int = 30,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Generate and return a PDF report for a site.

    Rate limited to 5 PDF generations per hour per user.
    """
    site = _get_accessible_site_or_404(db, site_id, current_user)

    organization = _get_organization_for_site(db, site_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    aggregator = ReportAggregator(db)
    report_data = aggregator.generate_pdf_report_data(
        site_id=site_id,
        site_name=site.url,
        period_days=min(period_days, 90),
    )

    try:
        validated_logo_url = validate_logo_url(organization.agency_logo_url)
        validated_color = validate_hex_color(organization.agency_primary_color)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    branding = BrandingConfig(
        agency_name=organization.agency_name,
        agency_logo_url=validated_logo_url,
        agency_primary_color=validated_color,
        agency_contact_email=organization.agency_contact_email,
    )

    pdf_generator = PDFGenerator()
    pdf_bytes = await pdf_generator.generate_pdf_async(report_data, branding)

    filename = f"report_{site.url.replace('://', '_').replace('/', '_')}_{datetime.utcnow().strftime('%Y%m%d')}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


@router.post(
    "/sites/{site_id}/report/preview",
    response_model=HTMLPreviewResponse,
    dependencies=[
        Depends(RateLimiter(times=10, seconds=3600, callback=rate_limit_callback))
    ],
)
async def preview_html_report(
    site_id: str,
    query: PDFReportQuery = PDFReportQuery(),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLPreviewResponse:
    """Return HTML preview of the report for debugging.

    Rate limited to 10 previews per hour per user.
    """
    site = _get_accessible_site_or_404(db, site_id, current_user)

    organization = _get_organization_for_site(db, site_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    aggregator = ReportAggregator(db)
    report_data = aggregator.generate_pdf_report_data(
        site_id=site_id,
        site_name=site.url,
        period_days=query.period_days,
    )

    try:
        validated_logo_url = validate_logo_url(organization.agency_logo_url)
        validated_color = validate_hex_color(organization.agency_primary_color)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    branding = BrandingConfig(
        agency_name=organization.agency_name,
        agency_logo_url=validated_logo_url,
        agency_primary_color=validated_color,
        agency_contact_email=organization.agency_contact_email,
    )

    pdf_generator = PDFGenerator()
    html_content = pdf_generator._render_html(report_data, branding)

    return HTMLPreviewResponse(html=html_content)
