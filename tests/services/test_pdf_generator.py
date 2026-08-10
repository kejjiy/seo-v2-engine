"""Unit tests for PDFGenerator service."""
import pytest
from datetime import datetime, timedelta

from app.services.reporting.pdf_generator import PDFGenerator, BrandingConfig
from app.services.reporting.aggregator import (
    PDFReportData,
    ReportPeriod,
    IMSTrendPoint,
    PageFixed,
    IssuesPrevented,
    TopImprovement,
)


@pytest.fixture
def sample_report_data():
    """Create sample PDFReportData for testing."""
    return PDFReportData(
        site_id="test-site-uuid",
        site_name="https://example.com",
        report_period=ReportPeriod(
            start_date="2026-02-26",
            end_date="2026-03-26",
        ),
        ims_trend=[
            IMSTrendPoint(date="2026-03-20", score=75, change=5),
            IMSTrendPoint(date="2026-03-21", score=78, change=3),
            IMSTrendPoint(date="2026-03-22", score=80, change=2),
        ],
        pages_fixed=[
            PageFixed(
                url="Page #1",
                fixed_at="2026-03-20 10:30",
                original_snippet="<h1>Old Title</h1>",
                new_snippet="<h1>Optimized Title</h1>",
            ),
            PageFixed(
                url="Page #2",
                fixed_at="2026-03-21 14:00",
                original_snippet="<meta name='description' content=''>",
                new_snippet="<meta name='description' content='New description'>",
            ),
        ],
        issues_prevented=IssuesPrevented(
            critical=1,
            warning=3,
            info=10,
            total=14,
        ),
        top_improvements=[
            TopImprovement(title="Added meta description", impact=10),
            TopImprovement(title="Fixed H1 tag", impact=5),
        ],
        site_health_score=80,
    )


@pytest.fixture
def default_branding():
    """Create default branding config."""
    return BrandingConfig()


@pytest.fixture
def custom_branding():
    """Create custom branding config."""
    return BrandingConfig(
        agency_name="Test Agency",
        agency_logo_url="https://example.com/logo.png",
        agency_primary_color="#0066cc",
        agency_contact_email="contact@testagency.com",
    )


class TestBrandingConfig:
    """Tests for BrandingConfig class."""

    def test_default_branding_values(self):
        """Test default branding values when none provided."""
        branding = BrandingConfig()
        assert branding.agency_name == "Your Agency"
        assert branding.agency_logo_url is None
        assert branding.agency_primary_color == "#059669"
        assert branding.agency_contact_email == "contact@agency.com"

    def test_custom_branding_values(self, custom_branding):
        """Test custom branding values are set correctly."""
        assert custom_branding.agency_name == "Test Agency"
        assert custom_branding.agency_logo_url == "https://example.com/logo.png"
        assert custom_branding.agency_primary_color == "#0066cc"
        assert custom_branding.agency_contact_email == "contact@testagency.com"

    def test_partial_branding_values(self):
        """Test partial branding values with defaults."""
        branding = BrandingConfig(agency_name="My Agency")
        assert branding.agency_name == "My Agency"
        assert branding.agency_primary_color == "#059669"


class TestPDFGenerator:
    """Tests for PDFGenerator class."""

    def test_pdf_generator_initialization(self):
        """Test PDFGenerator initializes correctly."""
        generator = PDFGenerator()
        assert generator.env is not None
        assert generator.template is not None

    def test_render_html_with_data(self, sample_report_data, default_branding):
        """Test HTML rendering with report data."""
        generator = PDFGenerator()
        html = generator._render_html(sample_report_data, default_branding)

        assert "<!DOCTYPE html>" in html
        assert "Site Performance Report" in html
        assert sample_report_data.site_name in html
        assert "80/100" in html

    def test_render_html_with_custom_branding(
        self, sample_report_data, custom_branding
    ):
        """Test HTML rendering includes custom branding."""
        generator = PDFGenerator()
        html = generator._render_html(sample_report_data, custom_branding)

        assert custom_branding.agency_name in html
        assert custom_branding.agency_contact_email in html

    def test_render_html_no_seo_v2_mentions(self, sample_report_data, default_branding):
        """Test HTML template has no SEO-v2 mentions (white-label compliance)."""
        generator = PDFGenerator()
        html = generator._render_html(sample_report_data, default_branding)

        assert "SEO-v2" not in html
        assert "seo-v2" not in html
        assert "SEO V2" not in html

    def test_generate_pdf_returns_bytes(self, sample_report_data, default_branding):
        """Test PDF generation returns bytes."""
        generator = PDFGenerator()
        pdf_bytes = generator.generate_pdf(sample_report_data, default_branding)

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:4] == b"%PDF"

    def test_generate_pdf_with_none_branding(self, sample_report_data):
        """Test PDF generation with no branding uses defaults."""
        generator = PDFGenerator()
        pdf_bytes = generator.generate_pdf(sample_report_data, branding=None)

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0

    @pytest.mark.asyncio
    async def test_generate_pdf_async(self, sample_report_data, default_branding):
        """Test async PDF generation."""
        generator = PDFGenerator()
        pdf_bytes = await generator.generate_pdf_async(
            sample_report_data, default_branding
        )

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:4] == b"%PDF"

    def test_load_css_returns_content(self):
        """Test CSS loading returns content."""
        generator = PDFGenerator()
        css = generator._load_css()

        assert isinstance(css, str)
        assert "@page" in css or "body" in css

    def test_pdf_contains_ims_trend_data(self, sample_report_data, default_branding):
        """Test PDF contains IMS trend data."""
        generator = PDFGenerator()
        html = generator._render_html(sample_report_data, default_branding)

        assert "IMS Score Trend" in html
        for point in sample_report_data.ims_trend:
            assert point.date in html

    def test_pdf_contains_pages_fixed(self, sample_report_data, default_branding):
        """Test PDF contains pages fixed data."""
        generator = PDFGenerator()
        html = generator._render_html(sample_report_data, default_branding)

        assert "Pages Fixed" in html
        assert "Before:" in html
        assert "After:" in html

    def test_pdf_contains_issues_prevented(self, sample_report_data, default_branding):
        """Test PDF contains issues prevented data."""
        generator = PDFGenerator()
        html = generator._render_html(sample_report_data, default_branding)

        assert "Issues Prevented" in html
        assert "Critical" in html
        assert "Warnings" in html

    def test_pdf_generation_performance(self, sample_report_data, default_branding):
        """Test PDF generation completes within 10 seconds."""
        import time

        generator = PDFGenerator()
        start = time.time()
        pdf_bytes = generator.generate_pdf(sample_report_data, default_branding)
        elapsed = time.time() - start

        assert elapsed < 10.0, f"PDF generation took {elapsed:.2f}s, expected < 10s"
        assert len(pdf_bytes) > 0

    def test_empty_data_handling(self):
        """Test PDF generation with empty data."""
        empty_data = PDFReportData(
            site_id="empty-site",
            site_name="https://empty.com",
            report_period=ReportPeriod(
                start_date="2026-03-01",
                end_date="2026-03-26",
            ),
            ims_trend=[],
            pages_fixed=[],
            issues_prevented=IssuesPrevented(),
            top_improvements=[],
            site_health_score=0,
        )

        generator = PDFGenerator()
        pdf_bytes = generator.generate_pdf(empty_data)

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
