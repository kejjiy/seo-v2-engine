"""Site model for database mapping."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class Site(Base):
    """Represents a tracked site.

    Attributes:
        id: Primary key (UUID).
        organization_id: Organization ID mapping.
        url: Site URL.
        ims_score: Current IMS score.
        sector: Site industry/sector (e.g., "Plumbing", "Medical") classified by LLM.
        is_ymyl: Boolean indicating if it's a YMYL (High Risk) site.
        requires_manual_validation: When True, all rewriting jobs must be
            manually approved (High Caution / YMYL mode - AC 6).
        created_at: Creation timestamp.
    """

    __tablename__ = "sites"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    url: str = Column(Text, nullable=False)
    ims_score: int = Column(Integer, default=0)

    # Classification metadata
    sector: Optional[str] = Column(String(255), nullable=True)
    is_ymyl: Optional[bool] = Column(Boolean, nullable=True, default=False)
    requires_manual_validation: Optional[bool] = Column(
        Boolean,
        nullable=True,
        default=False,
        comment="High Caution mode - YMYL sites require human veto on rewrites",
    )
    veto_callback_secret_hash: Optional[str] = Column(Text, nullable=True)
    veto_callback_secret_updated_at: Optional[datetime] = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Site(id={self.id}, url={self.url}, sector={self.sector}, "
            f"is_ymyl={self.is_ymyl}, requires_manual_validation={self.requires_manual_validation})>"
        )
