from time import sleep

from celery import current_task

from .celery_app import celery_app


@celery_app.task(acks_late=True)
def test_celery(word: str) -> str:
    for i in range(1, 11):
        sleep(1)
        current_task.update_state(state="PROGRESS", meta={"process_percent": i * 10})
    return f"test task return {word}"


@celery_app.task(
    acks_late=True,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_sync_batch_task(self, site_id: str, batch: list) -> dict:
    """Process a batch of synced WP posts/pages for a given site.

    Each item is validated, then persisted (upsert) into the pages table
    scoped to *site_id* so that RLS integrity is maintained (Story 1.2).

    Args:
        site_id: UUID of the authenticated Site (from API Key).
        batch: List of dicts with keys post_id, title, content, url,
               date_modified.

    Returns:
        Summary dict with processed / skipped / error counts.
    """
    import logging
    from datetime import datetime, timezone

    from app.db.session import SessionLocal
    from app.models.job import Job
    from app.models.page import Page

    logger = logging.getLogger(__name__)
    logger.info("Processing sync batch of %d items for site %s", len(batch), site_id)

    processed = 0
    skipped = 0
    errors: list[str] = []

    try:
        with SessionLocal() as db_session:
            for item in batch:
                try:
                    raw_post_id = item.get("post_id")
                    if raw_post_id is None:
                        skipped += 1
                        continue

                    try:
                        post_id_int = int(raw_post_id)
                    except (ValueError, TypeError):
                        logger.error(
                            "Invalid post_id type for site %s: %s", site_id, raw_post_id
                        )
                        errors.append(f"invalid_post_id:{raw_post_id}")
                        continue

                    # 1. Rejection Guard: check if the user vetoed this change recently
                    rejected_job = (
                        db_session.query(Job)
                        .filter(
                            Job.site_id == site_id,
                            Job.page_id == post_id_int,
                            Job.decision_status == "rejected",
                        )
                        .order_by(Job.rejected_at.desc(), Job.created_at.desc())
                        .first()
                    )
                    if rejected_job is not None:
                        logger.info(
                            "Skipping post_id=%d for site=%s because last decision is rejected",
                            post_id_int,
                            site_id,
                        )
                        skipped += 1
                        continue

                    # 2. Real Upsert (Story 3.3)
                    url = item.get("url", "")
                    title = item.get("title")
                    content = item.get("content", "")
                    raw_modified = item.get("date_modified")

                    modified_at = None
                    if raw_modified:
                        try:
                            modified_at = datetime.fromisoformat(
                                raw_modified.replace("Z", "+00:00")
                            )
                        except ValueError:
                            pass

                    # Note: In a real prod env, we'd use postgres ON CONFLICT via session.execute
                    # but here we use a clean SQLAlchemy model lookup for MVP stability.
                    existing_page = (
                        db_session.query(Page)
                        .filter(Page.site_id == site_id, Page.url == url)
                        .first()
                    )

                    if existing_page:
                        existing_page.title = title
                        existing_page.html_size = len(content)
                        existing_page.raw_html = content
                        existing_page.crawled_at = modified_at or datetime.now(
                            timezone.utc
                        )
                    else:
                        new_page = Page(
                            site_id=site_id,
                            url=url,
                            title=title,
                            html_size=len(content),
                            raw_html=content,
                            status_code=200,
                            crawled_at=modified_at or datetime.now(timezone.utc),
                        )
                        db_session.add(new_page)

                    processed += 1
                except Exception as item_exc:
                    logger.error(
                        "Error processing post_id=%s for site %s: %s",
                        item.get("post_id"),
                        site_id,
                        item_exc,
                    )
                    errors.append(str(item_exc))

            db_session.commit()
    except Exception as exc:
        logger.error(
            "Database session failed for sync batch (site=%s): %s", site_id, exc
        )
        errors.append(f"db_session_error:{exc}")

    result = {
        "status": "processed",
        "site_id": site_id,
        "processed_count": processed,
        "skipped_count": skipped,
        "error_count": len(errors),
    }
    logger.info("Sync batch complete for site %s: %s", site_id, result)
    return result


@celery_app.task(
    acks_late=True,
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def run_full_crawl_task(
    self, site_id: str, start_url: str, hard_cap: int = 500
) -> dict:
    """Execute a full site crawl for the given site.

    Runs the async FullCrawler inside an event loop within the Celery
    worker process. Updates task state with progress during the crawl
    and persists discovered pages to the database.

    Args:
        site_id: UUID of the site to crawl.
        start_url: Root URL to begin crawling from.
        hard_cap: Maximum number of pages to crawl (plan quota).

    Returns:
        Summary dict with crawl statistics.
    """
    import asyncio
    import logging
    from datetime import datetime, timezone

    from app.db.session import SessionLocal
    from app.models.page import Page
    from app.services.crawler.full import CrawlConfig, FullCrawler

    logger = logging.getLogger(__name__)
    logger.info(
        "Starting full crawl task for site %s at %s (hard_cap=%d)",
        site_id,
        start_url,
        hard_cap,
    )

    # Update task state to "processing"
    self.update_state(
        state="PROCESSING",
        meta={"site_id": site_id, "status": "crawling", "pages_crawled": 0},
    )

    def progress_callback(crawled: int, discovered: int) -> None:
        self.update_state(
            state="PROCESSING",
            meta={
                "site_id": site_id,
                "status": "crawling",
                "pages_crawled": crawled,
                "pages_discovered": discovered,
            },
        )

    # Configure and run the crawler
    config = CrawlConfig(
        site_id=site_id,
        start_url=start_url,
        hard_cap=hard_cap,
    )
    crawler = FullCrawler(config, progress_callback=progress_callback)

    # Run async crawler in a new event loop (Celery workers are sync)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        crawl_result = loop.run_until_complete(crawler.crawl())
    except Exception as exc:
        logger.error("Crawl failed for site %s: %s", site_id, exc)
        return {
            "status": "failed",
            "site_id": site_id,
            "error": str(exc),
        }
    finally:
        loop.close()

    # Persist discovered pages and classification to the database
    persisted_count = 0
    db_errors: list[str] = []
    try:
        with SessionLocal() as db:
            # Update site classification (H2/H3 fix: always write values)
            from app.models.site import Site

            site = db.query(Site).filter(Site.id == site_id).first()
            if site:
                if crawl_result.sector:
                    site.sector = crawl_result.sector
                # H2 fix: always persist is_ymyl, even when False
                site.is_ymyl = crawl_result.is_ymyl
                # H3 fix: enforce High Caution mode for YMYL sites (AC 6)
                site.requires_manual_validation = crawl_result.is_ymyl
                if crawl_result.is_ymyl:
                    logger.warning(
                        "HIGH CAUTION: Site %s is marked as YMYL (sector=%s). "
                        "Future rewriting jobs require manual validation.",
                        site_id,
                        crawl_result.sector,
                    )
                elif crawl_result.sector:
                    logger.info(
                        "Site %s categorized as sector: %s",
                        site_id,
                        crawl_result.sector,
                    )
                db.commit()

            # M3 fix: batch commit — accumulate pages then commit once
            for page_result in crawl_result.pages:
                try:
                    # Upsert: update on URL conflict, insert otherwise
                    existing = (
                        db.query(Page)
                        .filter(Page.site_id == site_id, Page.url == page_result.url)
                        .first()
                    )
                    if existing:
                        existing.title = page_result.title
                        existing.h1_count = page_result.h1_count
                        existing.html_size = page_result.html_size
                        existing.raw_html = page_result.raw_html
                        existing.status_code = page_result.status_code
                        existing.crawled_at = page_result.crawled_at
                    else:
                        new_page = Page(
                            site_id=site_id,
                            url=page_result.url,
                            title=page_result.title,
                            h1_count=page_result.h1_count,
                            html_size=page_result.html_size,
                            raw_html=page_result.raw_html,
                            status_code=page_result.status_code,
                            crawled_at=page_result.crawled_at,
                        )
                        db.add(new_page)
                    persisted_count += 1
                except Exception as db_exc:
                    db_errors.append(f"DB error for {page_result.url}: {db_exc}")
                    logger.error(
                        "Failed to persist page %s for site %s: %s",
                        page_result.url,
                        site_id,
                        db_exc,
                    )
            # Single batch commit for all pages
            try:
                db.commit()
            except Exception as batch_exc:
                db.rollback()
                persisted_count = 0
                db_errors.append(f"Batch commit failed: {batch_exc}")
                logger.error("Batch commit failed for site %s: %s", site_id, batch_exc)
    except Exception as db_init_exc:
        logger.error("Database connection failed: %s", db_init_exc)
        db_errors.append(f"DB connection error: {db_init_exc}")

    result = {
        "status": "completed",
        "site_id": site_id,
        "total_crawled": crawl_result.total_crawled,
        "total_discovered": crawl_result.total_discovered,
        "pages_persisted": persisted_count,
        "quota_reached": crawl_result.quota_reached,
        "crawl_errors": len(crawl_result.errors),
        "db_errors": len(db_errors),
        "started_at": (
            crawl_result.started_at.isoformat() if crawl_result.started_at else None
        ),
        "finished_at": (
            crawl_result.finished_at.isoformat() if crawl_result.finished_at else None
        ),
    }

    logger.info("Full crawl task complete for site %s: %s", site_id, result)
    return result


@celery_app.task(
    acks_late=True,
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def send_single_report_email_task(
    self, site_id: str, email: str, html_content: str
) -> dict:
    import asyncio
    import logging

    from app.services.reporting.email_client import EmailService

    logger = logging.getLogger(__name__)
    email_service = EmailService()

    async def _send():
        return await email_service.send_email(
            to_email=email,
            subject="SEO-v2: Your Weekly Coffee Report",
            html_content=html_content,
        )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        success = loop.run_until_complete(_send())
        if not success:
            logger.error(
                "Failed to send report email to %s for site %s. Retrying...",
                email,
                site_id,
            )
            raise RuntimeError("email_send_failed")
    except Exception as exc:
        logger.error("Error during email send: %s", exc)
        raise self.retry(exc=exc)
    finally:
        loop.close()

    return {"status": "completed", "email": email, "site_id": site_id}


@celery_app.task(acks_late=True, bind=True, max_retries=1, default_retry_delay=60)
def run_site_audit_task(
    self,
    audit_id: str,
    site_id: str,
    start_url: str | None,
    start_stage: str = "crawl",
) -> dict:
    import asyncio
    import logging

    from jinja2 import Environment, FileSystemLoader
    from sqlalchemy import text

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.models.job import Job
    from app.models.organization import Organization
    from app.models.site import Site
    from app.services import audit_service
    from app.services.reporting.aggregator import ReportAggregator
    from app.services.reporting.email_client import EmailService
    from app.services.reporting.pdf_generator import BrandingConfig, PDFGenerator

    logger = logging.getLogger(__name__)

    def _fail(db, tracker, reason: str) -> dict:
        audit_service.set_audit_tracker_stage(
            db,
            tracker,
            stage="failed",
            status="failed",
            message=reason,
        )
        return {
            "status": "failed",
            "audit_id": audit_id,
            "site_id": site_id,
            "error": reason,
        }

    try:
        with SessionLocal() as db:
            tracker = db.query(Job).filter(Job.id == audit_id).first()
            site = db.query(Site).filter(Site.id == site_id).first()
            if not tracker or not site:
                return {
                    "status": "failed",
                    "audit_id": audit_id,
                    "error": "missing_audit_or_site",
                }

            if start_stage == "crawl":
                audit_service.set_audit_tracker_stage(
                    db,
                    tracker,
                    stage="crawling",
                    status="running",
                    message="Crawl in progress.",
                )
                crawl_result = run_full_crawl_task.run(site_id, start_url or site.url)
                if crawl_result.get("status") != "completed":
                    return _fail(
                        db, tracker, crawl_result.get("error") or "crawl_failed"
                    )

            audit_service.set_audit_tracker_stage(
                db,
                tracker,
                stage="rewriting",
                status="running",
                message="Rewriting eligible pages.",
            )
            pages = audit_service.get_eligible_pages(db, site_id)
            if not pages:
                audit_service.set_audit_tracker_stage(
                    db,
                    tracker,
                    stage="generating_report",
                    status="running",
                    message="No eligible pages found. Preparing advisory report.",
                )
            else:
                jobs = audit_service.build_rewrite_jobs_for_pages(db, site_id, pages)
                for index, job in enumerate(jobs, start=1):
                    result = rewrite_content_task.run(str(job.id), str(job.page_id))
                    if result.get("status") == "failed":
                        logger.warning("Rewrite failed for job %s: %s", job.id, result)
                    audit_service.set_audit_tracker_stage(
                        db,
                        tracker,
                        stage="rewriting",
                        status="running",
                        message=f"Processed {index}/{len(jobs)} pages for audit.",
                        retry_count=index,
                    )

            audit_service.set_audit_tracker_stage(
                db,
                tracker,
                stage="generating_report",
                status="running",
                message="Generating advisory report and notifying stakeholders.",
            )

            aggregator = ReportAggregator(db)
            report_data = aggregator.generate_pdf_report_data(
                site_id=site_id, site_name=site.url, period_days=30
            )
            organization = (
                db.query(Organization)
                .filter(Organization.id == site.organization_id)
                .first()
            )
            if not organization:
                return _fail(db, tracker, "organization_not_found")

            branding = BrandingConfig(
                agency_name=organization.agency_name,
                agency_logo_url=organization.agency_logo_url,
                agency_primary_color=organization.agency_primary_color or "#059669",
                agency_contact_email=organization.agency_contact_email,
            )
            pdf_generator = PDFGenerator()
            asyncio.run(pdf_generator.generate_pdf_async(report_data, branding))

            env = Environment(
                loader=FileSystemLoader("app/services/reporting/templates")
            )
            template = env.get_template("audit_report_ready.html")
            email_service = EmailService()

            users = db.execute(
                text(
                    """
                    SELECT u.email
                    FROM public.users u
                    JOIN public.members m ON m.user_id = u.id
                    WHERE m.organization_id = :org_id
                    """
                ),
                {"org_id": site.organization_id},
            ).fetchall()
            report_url = f"{settings.DASHBOARD_BASE_URL.rstrip('/')}/dashboard/sites/{site_id}/report"
            email_errors = 0
            for row in users:
                email = row[0]
                html_content = template.render(
                    site_url=site.url,
                    report_url=report_url,
                    total_recommendations=len(report_data.pages_fixed),
                    health_score=report_data.site_health_score,
                )
                if not asyncio.run(
                    email_service.send_email(
                        to_email=email,
                        subject="SEO-v2: Your audit report is ready",
                        html_content=html_content,
                    )
                ):
                    email_errors += 1

            final_message = "Audit completed. PDF is available in the dashboard."
            if email_errors:
                final_message = (
                    f"Audit completed, but {email_errors} email(s) could not be sent."
                )

            audit_service.set_audit_tracker_stage(
                db,
                tracker,
                stage="completed",
                status="completed",
                message=final_message,
            )
            return {
                "status": "completed",
                "audit_id": audit_id,
                "site_id": site_id,
                "email_errors": email_errors,
            }
    except Exception as exc:
        logger.error("Audit workflow failed for site %s: %s", site_id, exc)
        try:
            with SessionLocal() as db:
                tracker = db.query(Job).filter(Job.id == audit_id).first()
                if tracker:
                    audit_service.set_audit_tracker_stage(
                        db,
                        tracker,
                        stage="failed",
                        status="failed",
                        message=str(exc),
                    )
        except Exception:
            logger.exception("Failed to persist audit failure state for %s", audit_id)
        return {
            "status": "failed",
            "audit_id": audit_id,
            "site_id": site_id,
            "error": str(exc),
        }


@celery_app.task(
    acks_late=True,
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def send_weekly_reports_task(self) -> dict:
    import logging

    from jinja2 import Environment, FileSystemLoader
    from sqlalchemy import text

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services import veto_service
    from app.services.reporting.aggregator import ReportAggregator

    logger = logging.getLogger(__name__)

    try:
        env = Environment(loader=FileSystemLoader("app/services/reporting/templates"))
        template = env.get_template("coffee_report.html")
    except Exception as exc:
        logger.error("Failed to load Jinja2 template: %s", exc)
        return {"status": "failed", "error": str(exc)}

    dispatched = 0
    try:
        with SessionLocal() as db:
            # Trial organizations must also receive reports during onboarding.
            query = text("""
                SELECT s.id, s.organization_id
                FROM sites s
                JOIN organizations o ON s.organization_id = o.id
                WHERE o.subscription_status IN ('active', 'trial')
            """)
            active_sites = db.execute(query).fetchall()

            aggregator = ReportAggregator(db)

            for site in active_sites:
                try:
                    site_id_value = getattr(site, "id", None)
                    org_id_value = getattr(site, "organization_id", None)
                    if site_id_value is None and isinstance(site, (tuple, list)):
                        site_id_value = site[0] if len(site) > 0 else None
                    if org_id_value is None and isinstance(site, (tuple, list)):
                        org_id_value = site[1] if len(site) > 1 else None

                    if site_id_value is None or org_id_value is None:
                        logger.error("Skipping site row with invalid shape: %s", site)
                        continue

                    site_id_str = str(site_id_value)
                    report_data = aggregator.generate_report(site_id_str)

                    change_rows = []
                    for change in report_data.change_rows:
                        if change.decision_status != "applied":
                            continue

                        token = veto_service.create_veto_event(
                            db,
                            organization_id=str(org_id_value),
                            site_id=site_id_str,
                            job_id=change.job_id,
                            ttl_hours=settings.VETO_TOKEN_TTL_HOURS,
                        )
                        change_rows.append(
                            {
                                "job_id": change.job_id,
                                "action_taken": change.action_taken,
                                "veto_url": veto_service.build_veto_url(
                                    settings.DASHBOARD_BASE_URL,
                                    token,
                                ),
                            }
                        )

                    html_content = template.render(
                        data=report_data, change_rows=change_rows
                    )

                    user_query = text("""
                    SELECT u.email FROM users u
                    JOIN members m ON u.id = m.user_id
                    WHERE m.organization_id = :org_id
                    """)
                    users = db.execute(user_query, {"org_id": org_id_value}).fetchall()

                    for user in users:
                        email = user[0]
                        send_single_report_email_task.delay(
                            site_id_str, email, html_content
                        )
                        dispatched += 1
                except Exception as exe:
                    logger.error(
                        "Error dispatching email for site %s: %s",
                        site_id_value if "site_id_value" in locals() else site,
                        exe,
                    )
    except Exception as db_exc:
        logger.error("DB error: %s", db_exc)
        return {"status": "failed", "error": str(db_exc)}

    return {"status": "completed", "dispatched_count": dispatched}


@celery_app.task(
    acks_late=True,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_veto_rollback_task(self, veto_event_id: str) -> dict:
    """Execute one veto rollback callback against WordPress plugin."""
    import hashlib
    import hmac
    import json
    import logging
    from time import time

    import httpx
    from celery.exceptions import MaxRetriesExceededError
    from sqlalchemy import text

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services import veto_service

    logger = logging.getLogger(__name__)

    def _fail(db, job_id: str | None, reason: str) -> dict:
        veto_service.mark_veto_event_result(
            db,
            veto_event_id=veto_event_id,
            status="failed",
            failure_reason=reason,
        )
        if job_id:
            veto_service.mark_job_rejection_failure_reason(
                db,
                job_id=job_id,
                failure_reason=reason,
            )
        return {"status": "failed", "veto_event_id": veto_event_id, "reason": reason}

    try:
        with SessionLocal() as db:
            row = db.execute(
                text(
                    """
                    SELECT
                        ve.id,
                        ve.status,
                        ve.job_id,
                        ve.site_id,
                        s.url,
                        s.veto_callback_secret_hash,
                        j.page_id
                    FROM public.veto_events ve
                    JOIN public.sites s ON s.id = ve.site_id
                    LEFT JOIN public.jobs j ON j.id = ve.job_id
                    WHERE ve.id = :veto_event_id
                    """
                ),
                {"veto_event_id": veto_event_id},
            ).fetchone()

            if not row:
                return {
                    "status": "failed",
                    "veto_event_id": veto_event_id,
                    "reason": "missing_veto_event",
                }

            if row.status == "rolled_back":
                return {"status": "rolled_back", "veto_event_id": veto_event_id}
            if row.status == "failed":
                return {"status": "failed", "veto_event_id": veto_event_id}
            if row.status != "confirmed":
                return {
                    "status": "ignored",
                    "veto_event_id": veto_event_id,
                    "reason": f"status_{row.status}",
                }

            if row.page_id is None:
                return _fail(db, str(row.job_id), "missing_post_id")

            try:
                post_id_int = int(row.page_id)
            except (ValueError, TypeError):
                return _fail(db, str(row.job_id), "invalid_post_id")

            if not row.url:
                return _fail(db, str(row.job_id), "missing_site_url")
            if not row.veto_callback_secret_hash:
                return _fail(db, str(row.job_id), "missing_callback_secret")

            payload = {
                "veto_event_id": str(row.id),
                "site_id": str(row.site_id),
                "job_id": str(row.job_id),
                "post_id": post_id_int,
            }
            raw_body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            timestamp = str(int(time()))
            signature = hmac.new(
                str(row.veto_callback_secret_hash).encode("utf-8"),
                f"{timestamp}.{raw_body}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            endpoint = f"{str(row.url).rstrip('/')}{settings.WP_VETO_ENDPOINT_PATH}"
            response = httpx.post(
                endpoint,
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-SEO-V2-Timestamp": timestamp,
                    "X-SEO-V2-Signature": signature,
                },
                timeout=settings.WP_ROLLBACK_TIMEOUT_SECONDS,
            )

            try:
                body = response.json()
            except ValueError:
                body = {}

            remote_status = body.get("status")
            if response.status_code == 200 and remote_status in {
                "rolled_back",
                "already_processed",
            }:
                veto_service.mark_veto_event_result(
                    db,
                    veto_event_id=veto_event_id,
                    status="rolled_back",
                )
                return {
                    "status": "rolled_back",
                    "veto_event_id": veto_event_id,
                    "wp_status": remote_status,
                }

            failure_reason = body.get("error_code") or f"http_{response.status_code}"
            failed_payload = _fail(db, str(row.job_id), failure_reason)

            if response.status_code >= 500:
                raise RuntimeError(f"rollback_http_{response.status_code}")
            return failed_payload

    except (httpx.RequestError, httpx.TimeoutException, RuntimeError) as exc:
        logger.error(
            "Rollback delivery failed for veto_event=%s: %s", veto_event_id, exc
        )
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            with SessionLocal() as db:
                return _fail(db, None, "temporary_error")


@celery_app.task(
    acks_late=True,
    bind=True,
    max_retries=5,
    default_retry_delay=60,
)
def rewrite_content_task(self, job_id: str, page_id: str) -> dict:
    """Execute LLM content rewrite for a given job/page.

    Flow:
    1. Fetch page content from DB
    2. Fetch site's is_ymyl, sector, requires_manual_validation
    3. Compute IMS before
    4. Call ContentRewriter.rewrite() with appropriate mode
    5. Compute IMS after
    6. Validate IMS gain threshold
    7. Store results in job record
    8. Trigger Story 3.7 (Quality Check) as next step

    Args:
        job_id: UUID of the job record.
        page_id: UUID of the page to rewrite.

    Returns:
        Summary dict with rewrite statistics.
    """
    import asyncio
    import logging
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.models.job import Job
    from app.models.page import Page
    from app.models.site import Site
    from app.services.ims_calculator import calculate_ims
    from app.services.llm.rewriter import ContentRewriter, RewriteError

    logger = logging.getLogger(__name__)
    logger.info("Starting rewrite task for job %s, page %s", job_id, page_id)

    async def _do_rewrite():
        with SessionLocal() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                raise ValueError(f"Job {job_id} not found")

            page = db.query(Page).filter(Page.id == page_id).first()
            if not page:
                raise ValueError(f"Page {page_id} not found")

            site = db.query(Site).filter(Site.id == job.site_id).first()
            if not site:
                raise ValueError(f"Site {job.site_id} not found")

            html_content = page.raw_html or ""
            if not html_content.strip():
                job.status = "failed"
                job.rewrite_status = "failed"
                job.changes_summary = "No HTML content available for rewriting"
                db.commit()
                return {"status": "failed", "job_id": job_id, "reason": "no_content"}

            ims_before = calculate_ims(html_content).score

            rewriter = ContentRewriter()
            try:
                result = await rewriter.rewrite(
                    html_content=html_content,
                    is_ymyl=site.is_ymyl or False,
                    requires_manual_validation=site.requires_manual_validation or False,
                    temperature=getattr(settings, "OPENAI_REWRITE_TEMPERATURE", 0.0),
                )
            except RewriteError as e:
                if "Circuit breaker is open" in str(e):
                    job.status = "pending_retry"
                    job.rewrite_status = "pending_retry"
                    job.retry_count = (job.retry_count or 0) + 1
                    job.last_retry_at = datetime.now(timezone.utc)
                    db.commit()
                    raise self.retry(exc=e)

                job.status = "failed"
                job.rewrite_status = "failed"
                job.changes_summary = str(e)
                db.commit()
                return {"status": "failed", "job_id": job_id, "reason": str(e)}

            rewritten_html = result.rewritten_html
            ims_after = calculate_ims(rewritten_html).score

            min_ims_gain = getattr(settings, "OPENAI_REWRITE_MIN_IMS_GAIN", 5)
            ims_gain = ims_after - ims_before

            if ims_gain <= 0:
                logger.warning(
                    "Rewrite did not improve IMS score (before=%d, after=%d)",
                    ims_before,
                    ims_after,
                )
                job.status = "completed"
                job.rewrite_status = "review_needed"
                job.changes_summary = f"IMS did not improve (was {ims_before}, now {ims_after}). Review recommended."
            elif ims_gain < min_ims_gain:
                logger.info(
                    "Rewrite improved IMS by %d, below threshold of %d",
                    ims_gain,
                    min_ims_gain,
                )
                job.status = "completed"
                job.rewrite_status = "review_needed"
                job.changes_summary = f"IMS gain ({ims_gain}) below threshold ({min_ims_gain}). {result.changes_summary}"
            else:
                job.status = "completed"
                job.rewrite_status = "success"
                job.changes_summary = result.changes_summary

            if site.requires_manual_validation or site.is_ymyl:
                job.rewrite_status = "review_needed"
                job.changes_summary = f"[YMYL/MANUAL VALIDATION] {job.changes_summary}"

            if job.rewrite_status == "success":
                job.action_taken = (
                    f"Prepared a priority SEO recommendation for {page.url}"
                )
            elif job.rewrite_status == "review_needed":
                job.action_taken = f"Prepared a draft recommendation for {page.url}"

            job.original_content = html_content
            job.rewritten_content = rewritten_html
            job.confidence_score = result.confidence_score
            job.llm_model_used = getattr(settings, "OPENAI_REWRITE_MODEL", "gpt-4o")
            job.prompt_version = "v1.0"
            job.ims_before = ims_before
            job.ims_after = ims_after
            job.ims_improvement = ims_gain
            job.new_snippet = rewritten_html[:500] if rewritten_html else None
            job.original_snippet = html_content[:500] if html_content else None

            db.commit()

            return {
                "status": "completed",
                "job_id": job_id,
                "page_id": page_id,
                "ims_before": ims_before,
                "ims_after": ims_after,
                "ims_gain": ims_gain,
                "confidence_score": result.confidence_score,
                "rewrite_status": job.rewrite_status,
            }

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_do_rewrite())
    except Exception as exc:
        logger.error("Rewrite task failed for job %s: %s", job_id, exc)
        return {"status": "failed", "job_id": job_id, "error": str(exc)}
    finally:
        loop.close()

    logger.info("Rewrite task complete for job %s: %s", job_id, result)
    return result


@celery_app.task(
    acks_late=True,
    bind=True,
)
def retry_pending_llm_jobs_task(self) -> dict:
    """Retry jobs that are in pending_retry status.

    Scheduled via Celery Beat every 15 minutes.
    Re-queues jobs that failed due to transient LLM API errors.
    """
    import logging

    from app.db.session import SessionLocal
    from app.models.job import Job

    logger = logging.getLogger(__name__)
    logger.info("Checking for pending LLM jobs to retry")

    retried_count = 0
    try:
        with SessionLocal() as db:
            pending_jobs = (
                db.query(Job).filter(Job.rewrite_status == "pending_retry").all()
            )

            for job in pending_jobs:
                if job.page_id:
                    rewrite_content_task.delay(str(job.id), str(job.page_id))
                    retried_count += 1
                    logger.info("Re-queued job %s for retry", job.id)

    except Exception as exc:
        logger.error("Failed to retry pending LLM jobs: %s", exc)
        return {"status": "failed", "error": str(exc)}

    logger.info("Re-queued %d pending LLM jobs", retried_count)
    return {"status": "completed", "retried_count": retried_count}
