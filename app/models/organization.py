"""Organization model for database mapping."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class Organization(Base):
    """Represents an organization with branding settings.

    Attributes:
        id: Primary key (UUID).
        name: Organization name.
        subscription_status: Current subscription status.
        agency_name: Display name for agency in PDF reports.
        agency_logo_url: URL to agency logo image.
        agency_primary_color: Hex color for branding.
        agency_contact_email: Contact email for reports.
        created_at: Creation timestamp.
    """

    __tablename__ = "organizations"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: str = Column(Text, nullable=False)
    subscription_status: str = Column(String(50), default="active")
    agency_name: Optional[str] = Column(Text, nullable=True)
    agency_logo_url: Optional[str] = Column(Text, nullable=True)
    agency_primary_color: Optional[str] = Column(
        Text, nullable=True, default="#059669"
    )
    agency_contact_email: Optional[str] = Column(Text, nullable=True)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, name={self.name})>"
