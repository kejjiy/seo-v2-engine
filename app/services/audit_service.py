from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.page import Page
from app.models.site import Site

AUDIT_TRACKER_PROMPT_VERSION = "audit_mvp_v1"
AUDIT_STAGES = [
    "queued",
    "crawling",
    "rewriting",
    "generating_report",
    "completed",
    "failed",
]
TERMINAL_AUDIT_STAGES = {"completed", "failed"}


@dataclass
class AuditTrackerMetrics:
    eligible_pages: int = 0
    rewritten_pages: int = 0
    failed_pages: int = 0
    review_pages: int = 0


def get_accessible_site(
    db: Session, site_id: str, user_id: Optional[str]
) -> Optional[Site]:
    if not user_id:
        return None

    row = db.execute(
        text(
            """
            SELECT s.id
            FROM public.sites s
            JOIN public.members m ON m.organization_id = s.organization_id
            WHERE s.id = :site_id AND m.user_id = :user_id
            LIMIT 1
            """
        ),
        {"site_id": site_id, "user_id": user_id},
    ).fetchone()
    if not row:
        return None

    return db.query(Site).filter(Site.id == site_id).first()


def get_latest_audit_tracker(db: Session, site_id: str) -> Optional[Job]:
    return (
        db.query(Job)
        .filter(
            Job.site_id == site_id,
            Job.page_id.is_(None),
            Job.prompt_version == AUDIT_TRACKER_PROMPT_VERSION,
        )
        .order_by(Job.created_at.desc())
        .first()
    )


def create_audit_tracker(db: Session, site_id: str, stage: str = "queued") -> Job:
    tracker = Job(
        site_id=site_id,
        page_id=None,
        status="queued",
        decision_status="system",
        rewrite_status=stage,
        prompt_version=AUDIT_TRACKER_PROMPT_VERSION,
        changes_summary="Audit queued from dashboard.",
    )
    db.add(tracker)
    db.commit()
    db.refresh(tracker)
    return tracker


def set_audit_tracker_stage(
    db: Session,
    tracker: Job,
    *,
    stage: str,
    status: str,
    message: Optional[str] = None,
    retry_count: Optional[int] = None,
) -> Job:
    tracker.rewrite_status = stage
    tracker.status = status
    if message is not None:
        tracker.changes_summary = message
    if retry_count is not None:
        tracker.retry_count = retry_count
    db.add(tracker)
    db.commit()
    db.refresh(tracker)
    return tracker


def get_eligible_pages(db: Session, site_id: str) -> list[Page]:
    return (
        db.query(Page)
        .filter(Page.site_id == site_id)
        .filter(Page.raw_html.is_not(None))
        .filter(Page.raw_html != "")
        .order_by(Page.crawled_at.desc().nullslast(), Page.id.asc())
        .all()
    )


def build_rewrite_jobs_for_pages(
    db: Session, site_id: str, pages: list[Page]
) -> list[Job]:
    jobs: list[Job] = []
    for page in pages:
        job = Job(
            site_id=site_id,
            page_id=page.id,
            status="queued",
            decision_status="applied",
            rewrite_status="pending",
            prompt_version="rewrite_mvp_v1",
        )
        db.add(job)
        jobs.append(job)

    db.commit()
    for job in jobs:
        db.refresh(job)
    return jobs


def get_audit_tracker_metrics(
    db: Session, site_id: str, tracker_created_at
) -> AuditTrackerMetrics:
    metrics = AuditTrackerMetrics()
    jobs = (
        db.query(Job)
        .filter(
            Job.site_id == site_id,
            Job.page_id.is_not(None),
            Job.created_at >= tracker_created_at,
        )
        .all()
    )
    metrics.eligible_pages = len(jobs)
    for job in jobs:
        if job.rewrite_status == "success":
            metrics.rewritten_pages += 1
        elif job.rewrite_status == "review_needed":
            metrics.review_pages += 1
        elif job.rewrite_status == "failed":
            metrics.failed_pages += 1
    return metrics


def build_audit_status_payload(db: Session, site: Site, tracker: Optional[Job]) -> dict:
    if tracker is None:
        return {
            "audit_id": None,
            "site_id": str(site.id),
            "status": "idle",
            "current_stage": None,
            "progress_percent": 0,
            "message": "No audit has been launched yet.",
            "stages": [
                {"name": name, "status": "pending"} for name in AUDIT_STAGES[:-2]
            ]
            + [{"name": "completed", "status": "pending"}],
            "rewrite": {
                "eligible_pages": 0,
                "rewritten_pages": 0,
                "failed_pages": 0,
                "review_pages": 0,
            },
            "report": {"pdf_ready": False, "delivery": "dashboard_link"},
            "error_message": None,
        }

    metrics = get_audit_tracker_metrics(db, str(site.id), tracker.created_at)
    stage = tracker.rewrite_status or "queued"
    stage_statuses: list[dict] = []
    for name in ["queued", "crawling", "rewriting", "generating_report", "completed"]:
        if stage == "failed" and name == "completed":
            stage_state = "pending"
        elif (
            AUDIT_STAGES.index(name) < AUDIT_STAGES.index(stage)
            if stage in AUDIT_STAGES and name in AUDIT_STAGES
            else False
        ):
            stage_state = "completed"
        elif stage == name:
            stage_state = "failed" if tracker.status == "failed" else "active"
        else:
            stage_state = "pending"
        stage_statuses.append({"name": name, "status": stage_state})

    if stage == "rewriting" and metrics.eligible_pages:
        rewrite_done = (
            metrics.rewritten_pages + metrics.failed_pages + metrics.review_pages
        )
        progress_percent = 25 + int((rewrite_done / metrics.eligible_pages) * 55)
    else:
        progress_percent = {
            "queued": 5,
            "crawling": 20,
            "rewriting": 35,
            "generating_report": 90,
            "completed": 100,
            "failed": 100,
        }.get(stage, 0)

    error_message = tracker.changes_summary if tracker.status == "failed" else None
    return {
        "audit_id": str(tracker.id),
        "site_id": str(site.id),
        "status": stage,
        "current_stage": stage,
        "progress_percent": progress_percent,
        "message": tracker.changes_summary,
        "stages": stage_statuses,
        "rewrite": {
            "eligible_pages": metrics.eligible_pages,
            "rewritten_pages": metrics.rewritten_pages,
            "failed_pages": metrics.failed_pages,
            "review_pages": metrics.review_pages,
        },
        "report": {
            "pdf_ready": stage == "completed",
            "delivery": "dashboard_link",
        },
        "error_message": error_message,
    }
