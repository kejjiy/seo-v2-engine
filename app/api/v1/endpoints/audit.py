from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.services import audit_service
from app.worker.celery_worker import run_site_audit_task

router = APIRouter()


class AuditLaunchResponse(BaseModel):
    audit_id: str
    site_id: str
    status: str
    message: str


class AuditStatusResponse(BaseModel):
    audit_id: str | None
    site_id: str
    status: str
    current_stage: str | None
    progress_percent: int
    message: str | None
    stages: list[dict]
    rewrite: dict
    report: dict
    error_message: str | None


def _get_site_or_404(db: Session, site_id: str, current_user: dict):
    site = audit_service.get_accessible_site(db, site_id, current_user.get("user_id"))
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


def _guard_against_parallel_audit(db: Session, site_id: str) -> None:
    tracker = audit_service.get_latest_audit_tracker(db, site_id)
    if (
        tracker
        and (tracker.rewrite_status or "queued")
        not in audit_service.TERMINAL_AUDIT_STAGES
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An audit is already running for this site.",
        )


@router.post("/sites/{site_id}/crawl", response_model=AuditLaunchResponse)
async def launch_site_audit_crawl(
    site_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    site = _get_site_or_404(db, site_id, current_user)
    _guard_against_parallel_audit(db, site_id)

    tracker = audit_service.create_audit_tracker(db, site_id, stage="queued")
    run_site_audit_task.delay(str(tracker.id), site_id, site.url, "crawl")
    return AuditLaunchResponse(
        audit_id=str(tracker.id),
        site_id=site_id,
        status="queued",
        message="Audit queued. Crawl will start shortly.",
    )


@router.post("/sites/{site_id}/rewrite", response_model=AuditLaunchResponse)
async def launch_site_audit_rewrite(
    site_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _get_site_or_404(db, site_id, current_user)
    _guard_against_parallel_audit(db, site_id)

    pages = audit_service.get_eligible_pages(db, site_id)
    if not pages:
        raise HTTPException(
            status_code=400, detail="No eligible pages available for rewrite."
        )

    tracker = audit_service.create_audit_tracker(db, site_id, stage="queued")
    run_site_audit_task.delay(str(tracker.id), site_id, None, "rewrite")
    return AuditLaunchResponse(
        audit_id=str(tracker.id),
        site_id=site_id,
        status="queued",
        message="Rewrite queued. Report generation will follow automatically.",
    )


@router.get("/sites/{site_id}/audit-status", response_model=AuditStatusResponse)
async def get_site_audit_status(
    site_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    site = _get_site_or_404(db, site_id, current_user)
    tracker = audit_service.get_latest_audit_tracker(db, site_id)
    return AuditStatusResponse(
        **audit_service.build_audit_status_payload(db, site, tracker)
    )
