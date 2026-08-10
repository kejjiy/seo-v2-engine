"""Pydantic schemas for Site classification metadata."""
from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class SiteBase(BaseModel):
    """Base schema for sites with shared properties."""
    url: str
    ims_score: int = 0
    sector: Optional[str] = Field(default=None, description="Site industry or sector")
    is_ymyl: Optional[bool] = Field(default=False, description="Flag indicating if site is Your Money or Your Life (High Caution)")
    requires_manual_validation: Optional[bool] = Field(
        default=False,
        description="When True, all rewriting jobs require human approval (YMYL High Caution mode)"
    )

class SiteCreate(SiteBase):
    """Schema for returning site creation request."""
    organization_id: UUID

class SiteUpdate(BaseModel):
    """Schema for updating a site record."""
    ims_score: Optional[int] = None
    sector: Optional[str] = None
    is_ymyl: Optional[bool] = None
    requires_manual_validation: Optional[bool] = None

class SiteRead(SiteBase):
    """Schema for reading a site record."""
    id: UUID
    organization_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
