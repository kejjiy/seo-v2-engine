"""Public one-click veto endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi_limiter.depends import RateLimiter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import RateLimitExceeded
from app.core.security import get_current_site
from app.db.session import get_db
from app.services import veto_service
from app.worker.celery_worker import process_veto_rollback_task

router = APIRouter()


class VetoConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class VetoConfirmResponse(BaseModel):
    state: Literal[
        "confirmed",
        "already_processed",
        "invalid_or_expired",
        "rollback_failed",
    ]
    message: str


class CallbackSecretRequest(BaseModel):
    secret_hash: str = Field(min_length=64, max_length=64)


async def rate_limit_callback(request: Request, response: Response, pexpire: int):
    raise RateLimitExceeded(
        detail=f"Rate limit exceeded. Try again in {pexpire} seconds."
    )


@router.post(
    "/veto/confirm",
    response_model=VetoConfirmResponse,
    dependencies=[
        Depends(RateLimiter(times=10, seconds=60, callback=rate_limit_callback))
    ],
)
async def confirm_veto(
    payload: VetoConfirmRequest,
    db: Session = Depends(get_db),
) -> VetoConfirmResponse:
    """Confirm a one-time veto token and trigger async rollback."""
    result = veto_service.confirm_veto_token(db, payload.token)
    if result.state == "confirmed" and result.veto_event_id:
        process_veto_rollback_task.delay(veto_event_id=result.veto_event_id)
    return VetoConfirmResponse(state=result.state, message=result.message)


@router.post(
    "/veto/callback-secret",
    dependencies=[
        Depends(RateLimiter(times=30, seconds=60, callback=rate_limit_callback))
    ],
)
async def register_callback_secret(
    payload: CallbackSecretRequest,
    current_site: dict = Depends(get_current_site),
    db: Session = Depends(get_db),
) -> dict:
    """Register plugin callback secret hash for secure rollback callbacks."""
    veto_service.register_callback_secret_hash(
        db,
        site_id=str(current_site["site_id"]),
        organization_id=str(current_site["organization_id"]),
        secret_hash=payload.secret_hash,
    )
    return {"status": "registered"}
