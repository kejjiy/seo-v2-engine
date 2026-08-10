from fastapi import APIRouter, Depends, status
from typing import List

from app.core.security import get_current_site
from app.schemas.sync import SyncItemIn, SyncBatchResponse
from app.worker.celery_worker import process_sync_batch_task

router = APIRouter()


@router.post(
    "/sync",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncBatchResponse,
)
async def receive_sync_batch(
    batch: List[SyncItemIn],
    current_site: dict = Depends(get_current_site),
) -> SyncBatchResponse:
    """Receive a batch of WP posts and queue them for background processing.

    Authenticates via X-API-Key, validates the payload, then immediately
    offloads the work to a Celery task so the HTTP connection is freed.
    """
    # Convert pydantic models to dicts for Celery JSON serialization
    batch_data = [
        {
            "post_id": item.post_id,
            "title": item.title,
            "content": item.content,
            "url": str(item.url),
            "date_modified": item.date_modified.isoformat(),
        }
        for item in batch
    ]
    process_sync_batch_task.delay(
        site_id=str(current_site["site_id"]),
        batch=batch_data,
    )

    return SyncBatchResponse(
        message="Sync batch accepted",
        queued_items=len(batch),
    )
