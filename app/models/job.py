"""Job model tracking SEO-v2 fixes and improvements."""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base

class Job(Base):
    __tablename__ = "jobs"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: uuid.UUID = Column(UUID(as_uuid=True), index=True, nullable=False)
    page_id: int = Column(Integer, nullable=True)
    status: str = Column(String(50), default="completed")
    action_taken: str = Column(Text, nullable=True)
    original_snippet: str = Column(Text, nullable=True)
    new_snippet: str = Column(Text, nullable=True)
    ims_improvement: int = Column(Integer, default=0)
    decision_status: str = Column(String(50), nullable=False, default="applied")
    rejected_at: datetime = Column(DateTime(timezone=True), nullable=True)
    rejection_source: str = Column(String(255), nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())

    # Content rewrite fields
    original_content: Optional[str] = Column(Text, nullable=True)
    rewritten_content: Optional[str] = Column(Text, nullable=True)
    changes_summary: Optional[str] = Column(Text, nullable=True)
    confidence_score: Optional[float] = Column(Float, nullable=True)
    llm_model_used: Optional[str] = Column(String(100), nullable=True)
    prompt_version: Optional[str] = Column(String(50), nullable=True)
    ims_before: Optional[int] = Column(Integer, nullable=True)
    ims_after: Optional[int] = Column(Integer, nullable=True)
    rewrite_status: Optional[str] = Column(
        String(50),
        nullable=True,
        default="pending",
        comment="pending/success/review_needed/failed"
    )
    retry_count: int = Column(Integer, default=0)
    last_retry_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
