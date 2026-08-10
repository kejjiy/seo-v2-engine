import resend
from typing import Optional
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        if settings.RESEND_API_KEY:
            resend.api_key = settings.RESEND_API_KEY
            self.enabled = True
        else:
            logger.warning("RESEND_API_KEY not set. Email service disabled.")
            self.enabled = False

    async def send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        """
        Sends an email using Resend.
        In a real application, you might want a domain configured in Resend.
        """
        if not self.enabled:
            logger.warning(f"Cannot send email to {to_email}. Service is disabled.")
            return False

        try:
            params = {
                "from": "Acme <onboarding@resend.dev>",
                "to": to_email,
                "subject": subject,
                "html": html_content,
            }
            email = resend.Emails.send(params)
            logger.info(f"Email sent successfully id={email.get('id')}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
