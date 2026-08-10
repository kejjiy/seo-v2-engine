"""PDF generation service for white-label agency reports."""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML, CSS

from app.services.reporting.aggregator import PDFReportData

TEMPLATES_DIR = Path(__file__).parent / "templates"


class BrandingConfig:
    """Agency branding configuration for PDF reports."""

    def __init__(
        self,
        agency_name: Optional[str] = None,
        agency_logo_url: Optional[str] = None,
        agency_primary_color: str = "#059669",
        agency_contact_email: Optional[str] = None,
    ):
        self.agency_name = agency_name or "Your Agency"
        self.agency_logo_url = agency_logo_url
        self.agency_primary_color = agency_primary_color
        self.agency_contact_email = agency_contact_email or "contact@agency.com"


class PDFGenerator:
    """Generate white-label PDF reports for agency clients."""

    def __init__(self, timeout_seconds: int = 30):
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.template = self.env.get_template("pdf_report.html")
        self.timeout_seconds = timeout_seconds

    def _render_html(self, data: PDFReportData, branding: BrandingConfig) -> str:
        """Render HTML template with report data and branding."""
        return self.template.render(
            data=data,
            branding=branding,
            generated_at=datetime.utcnow().strftime("%B %d, %Y"),
            primary_color=branding.agency_primary_color,
        )

    def _load_css(self, primary_color: str = "#059669") -> str:
        """Load PDF styles from CSS file with dynamic primary color."""
        css_path = TEMPLATES_DIR / "pdf_styles.css"
        if css_path.exists():
            css_content = css_path.read_text(encoding="utf-8")
            css_content = css_content.replace("#059669", primary_color)
            return css_content
        return ""

    def generate_pdf(
        self, data: PDFReportData, branding: Optional[BrandingConfig] = None
    ) -> bytes:
        """Generate PDF from report data.

        Args:
            data: PDFReportData containing all report metrics.
            branding: Optional branding configuration.

        Returns:
            PDF content as bytes.
        """
        if branding is None:
            branding = BrandingConfig()

        html_content = self._render_html(data, branding)
        css_content = self._load_css(branding.agency_primary_color)

        html_doc = HTML(string=html_content, base_url=str(TEMPLATES_DIR), timeout=self.timeout_seconds)
        css_doc = CSS(string=css_content) if css_content else None

        stylesheets = [css_doc] if css_doc else []
        pdf_bytes = html_doc.write_pdf(stylesheets=stylesheets)

        return pdf_bytes

    async def generate_pdf_async(
        self, data: PDFReportData, branding: Optional[BrandingConfig] = None
    ) -> bytes:
        """Generate PDF asynchronously using asyncio.to_thread.

        PDF generation is CPU-bound, so we offload to a thread.

        Args:
            data: PDFReportData containing all report metrics.
            branding: Optional branding configuration.

        Returns:
            PDF content as bytes.
        """
        return await asyncio.to_thread(self.generate_pdf, data, branding)
