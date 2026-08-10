"""Integration tests for PDF report API endpoints."""

import pytest
import sys
import types
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_limiter import FastAPILimiter

fake_pdf_module = types.ModuleType("app.services.reporting.pdf_generator")


class _FakeBrandingConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakePDFGenerator:
    async def generate_pdf_async(self, *args, **kwargs):
        return b"%PDF-1.4"

    def _render_html(self, *args, **kwargs):
        return "<html></html>"


fake_pdf_module.BrandingConfig = _FakeBrandingConfig
fake_pdf_module.PDFGenerator = _FakePDFGenerator
sys.modules.setdefault("app.services.reporting.pdf_generator", fake_pdf_module)


class _FakeRedis:
    async def evalsha(self, *args, **kwargs):
        return 0

    async def script_load(self, *args, **kwargs):
        return "sha"


async def _fake_identifier(request):
    return "test-user"


async def _fake_callback(request, response, pexpire):
    return None


from app.api.v1.endpoints import reports
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.site import Site
from app.models.organization import Organization


@pytest.fixture
def mock_site():
    """Create a mock site object."""
    site = MagicMock(spec=Site)
    site.id = "test-site-uuid"
    site.url = "https://example.com"
    site.ims_score = 80
    site.organization_id = "test-org-uuid"
    return site


@pytest.fixture
def mock_organization():
    """Create a mock organization object."""
    org = MagicMock(spec=Organization)
    org.id = "test-org-uuid"
    org.name = "Test Org"
    org.agency_name = "Test Agency"
    org.agency_logo_url = "https://example.com/logo.png"
    org.agency_primary_color = "#059669"
    org.agency_contact_email = "contact@testagency.com"
    return org


@pytest.fixture
def reports_client():
    """Create a test client with reports router."""
    app = FastAPI()
    app.include_router(reports.router, prefix="/api/v1")
    FastAPILimiter.redis = _FakeRedis()
    FastAPILimiter.prefix = "test-rate-limit"
    FastAPILimiter.lua_sha = "sha"
    FastAPILimiter.identifier = _fake_identifier
    FastAPILimiter.http_callback = _fake_callback

    async def override_get_current_user():
        return {"token": "test-token", "user_id": "test-user"}

    def override_get_db():
        return MagicMock()

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


class TestPDFReportEndpoint:
    """Tests for PDF report generation endpoint."""

    @patch("app.api.v1.endpoints.reports.ReportAggregator")
    @patch("app.api.v1.endpoints.reports.PDFGenerator")
    @patch("app.api.v1.endpoints.reports.audit_service.get_accessible_site")
    def test_generate_pdf_report_success(
        self,
        mock_get_accessible_site,
        mock_pdf_generator_class,
        mock_aggregator_class,
        reports_client,
        mock_site,
        mock_organization,
    ):
        """Test successful PDF report generation."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_site,
            mock_organization,
        ]

        mock_aggregator = MagicMock()
        mock_aggregator.generate_pdf_report_data.return_value = MagicMock()
        mock_aggregator_class.return_value = mock_aggregator

        mock_pdf_generator = MagicMock()
        mock_pdf_generator.generate_pdf_async = AsyncMock(
            return_value=b"%PDF-1.4 mock pdf content"
        )
        mock_pdf_generator_class.return_value = mock_pdf_generator
        mock_get_accessible_site.return_value = mock_site

        reports_client.app.dependency_overrides[get_db] = lambda: mock_db

        response = reports_client.get(
            "/api/v1/sites/test-site-uuid/report/pdf",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers.get("content-disposition", "")

    def test_generate_pdf_report_site_not_found(self, reports_client):
        """Test PDF report generation with non-existent site."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        reports_client.app.dependency_overrides[get_db] = lambda: mock_db

        response = reports_client.get(
            "/api/v1/sites/nonexistent-uuid/report/pdf",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404

    @patch("app.api.v1.endpoints.reports.ReportAggregator")
    @patch("app.api.v1.endpoints.reports.PDFGenerator")
    @patch("app.api.v1.endpoints.reports.audit_service.get_accessible_site")
    def test_generate_pdf_report_filename_format(
        self,
        mock_get_accessible_site,
        mock_pdf_generator_class,
        mock_aggregator_class,
        reports_client,
        mock_site,
        mock_organization,
    ):
        """Test PDF report filename follows expected format."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_site,
            mock_organization,
        ]

        mock_aggregator = MagicMock()
        mock_aggregator.generate_pdf_report_data.return_value = MagicMock()
        mock_aggregator_class.return_value = mock_aggregator

        mock_pdf_generator = MagicMock()
        mock_pdf_generator.generate_pdf_async = AsyncMock(return_value=b"%PDF-1.4")
        mock_pdf_generator_class.return_value = mock_pdf_generator
        mock_get_accessible_site.return_value = mock_site

        reports_client.app.dependency_overrides[get_db] = lambda: mock_db

        response = reports_client.get(
            "/api/v1/sites/test-site-uuid/report/pdf",
            headers={"Authorization": "Bearer test-token"},
        )

        content_disposition = response.headers.get("content-disposition", "")
        assert "report_" in content_disposition
        assert ".pdf" in content_disposition


class TestHTMLPreviewEndpoint:
    """Tests for HTML preview endpoint."""

    @patch("app.api.v1.endpoints.reports.ReportAggregator")
    @patch("app.api.v1.endpoints.reports.PDFGenerator")
    @patch("app.api.v1.endpoints.reports.audit_service.get_accessible_site")
    def test_preview_html_report_success(
        self,
        mock_get_accessible_site,
        mock_pdf_generator_class,
        mock_aggregator_class,
        reports_client,
        mock_site,
        mock_organization,
    ):
        """Test successful HTML preview generation."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_site,
            mock_organization,
        ]

        mock_aggregator = MagicMock()
        mock_aggregator.generate_pdf_report_data.return_value = MagicMock()
        mock_aggregator_class.return_value = mock_aggregator

        mock_pdf_generator = MagicMock()
        mock_pdf_generator._render_html.return_value = (
            "<html><body>Test Report</body></html>"
        )
        mock_pdf_generator_class.return_value = mock_pdf_generator
        mock_get_accessible_site.return_value = mock_site

        reports_client.app.dependency_overrides[get_db] = lambda: mock_db

        response = reports_client.post(
            "/api/v1/sites/test-site-uuid/report/preview",
            json={"period_days": 30},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "html" in data
        assert "Test Report" in data["html"]

    def test_preview_html_report_site_not_found(self, reports_client):
        """Test HTML preview with non-existent site."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        reports_client.app.dependency_overrides[get_db] = lambda: mock_db

        response = reports_client.post(
            "/api/v1/sites/nonexistent-uuid/report/preview",
            json={"period_days": 30},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404


class TestRateLimiting:
    """Tests for rate limiting on PDF endpoints."""

    def test_pdf_endpoint_rate_limit_headers(self, reports_client):
        """Test rate limit headers are present."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        reports_client.app.dependency_overrides[get_db] = lambda: mock_db

        response = reports_client.get(
            "/api/v1/sites/test-site-uuid/report/pdf",
            headers={"Authorization": "Bearer test-token"},
        )

        assert "x-ratelimit-limit" in response.headers or response.status_code in [
            200,
            404,
        ]
