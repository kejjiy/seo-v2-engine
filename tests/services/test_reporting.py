import pytest
from unittest.mock import patch
from app.services.reporting.email_client import EmailService


@pytest.mark.asyncio
@patch("app.services.reporting.email_client.settings.RESEND_API_KEY", "test_key")
async def test_email_service_sends_email_successfully():
    """Test that EmailService can send an email successfully using Resend."""
    with patch("app.services.reporting.email_client.resend.Emails.send") as mock_send:
        mock_send.return_value = {"id": "test_id"}

        service = EmailService()
        result = await service.send_email(
            to_email="user@example.com",
            subject="Weekly Coffee Report",
            html_content="<p>All green!</p>",
        )

        assert result is True
        mock_send.assert_called_once()
        params = mock_send.call_args[0][0]
        assert params["to"] == "user@example.com"
        assert params["subject"] == "Weekly Coffee Report"
        assert params["html"] == "<p>All green!</p>"


@pytest.mark.asyncio
@patch("app.services.reporting.email_client.settings.RESEND_API_KEY", "test_key")
async def test_email_service_handles_failure():
    """Test that EmailService handles errors gracefully."""
    with patch("app.services.reporting.email_client.resend.Emails.send") as mock_send:
        mock_send.side_effect = Exception("API Error")

        service = EmailService()
        result = await service.send_email(
            to_email="user@example.com",
            subject="Weekly Coffee Report",
            html_content="<p>All green!</p>",
        )

        assert result is False
