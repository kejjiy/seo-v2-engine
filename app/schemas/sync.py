"""Pydantic schemas for the sync endpoint.

Defines the request/response models for WP content synchronization.
"""
from datetime import datetime

from pydantic import BaseModel, HttpUrl


class SyncItemIn(BaseModel):
    """A single WP post/page submitted for sync."""
    post_id: int
    title: str
    content: str
    url: HttpUrl
    date_modified: datetime


class SyncBatchResponse(BaseModel):
    """Response returned after a sync batch is accepted."""
    message: str
    queued_items: int
