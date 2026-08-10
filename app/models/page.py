"""Page model for storing crawled site structure.

Each Page record represents a discovered URL during a full site crawl,
tied to a specific site_id (and implicitly to an organization via RLS).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base  # noqa: F401 — re-exported for backwards compat


class Page(Base):
    """Represents a crawled page discovered during a full site crawl.

    Attributes:
        id: Auto-incremented primary key.
        site_id: UUID of the parent site (foreign key enforced at DB level).
        url: Full canonical URL of the page.
        title: HTML <title> content (nullable).
        h1_count: Number of <h1> tags found on the page.
        html_size: Raw HTML content length in bytes.
        raw_html: Full HTML content of the page (for rewriting).
        status_code: HTTP status code returned when crawling.
        crawled_at: Timestamp of last successful crawl.
        created_at: Record creation timestamp (server default).
        updated_at: Record update timestamp (auto-updated).
    """

    __tablename__ = "pages"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    site_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False, index=True)
    url: str = Column(Text, nullable=False)
    title: Optional[str] = Column(Text, nullable=True)
    h1_count: int = Column(Integer, nullable=True, default=0)
    html_size: int = Column(Integer, nullable=True, default=0)
    raw_html: Optional[str] = Column(Text, nullable=True)
    status_code: int = Column(Integer, nullable=True)
    crawled_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("site_id", "url", name="uq_page_site_url"),
    )

    def __repr__(self) -> str:
        return f"<Page(id={self.id}, site_id={self.site_id}, url={self.url})>"
