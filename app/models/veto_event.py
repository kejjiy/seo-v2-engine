"""VetoEvent model for one-click rollback workflow."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class VetoEvent(Base):
    """Tracks one-time veto confirmations and rollback outcomes."""

    __tablename__ = "veto_events"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: uuid.UUID = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    site_id: uuid.UUID = Column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    token_hash: str = Column(Text, nullable=False, unique=True, index=True)
    status: str = Column(String(32), nullable=False, default="pending", index=True)
    expires_at: datetime = Column(DateTime(timezone=True), nullable=False)
    confirmed_at: datetime = Column(DateTime(timezone=True), nullable=True)
    rollback_completed_at: datetime = Column(DateTime(timezone=True), nullable=True)
    failure_reason: str = Column(Text, nullable=True)
    created_at: datetime = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
