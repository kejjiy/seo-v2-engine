import os

from celery import Celery
from celery.schedules import crontab

_CELERY_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    "redis://localhost:6379/0",
)
_CELERY_BROKER = os.getenv(
    "CELERY_BROKER_URL",
    "amqp://guest:guest@localhost:5672//",
)

celery_app = Celery(
    "worker",
    backend=_CELERY_BACKEND,
    broker=_CELERY_BROKER,
)

celery_app.conf.task_routes = {
    "app.worker.celery_worker.test_celery": "test-queue",
    "app.worker.celery_worker.process_sync_batch_task": "sync-queue",
    "app.worker.celery_worker.run_full_crawl_task": "crawl-queue",
    "app.worker.celery_worker.send_weekly_reports_task": "reporting-queue",
    "app.worker.celery_worker.send_single_report_email_task": "reporting-queue",
    "app.worker.celery_worker.process_veto_rollback_task": "reporting-queue",
    "app.worker.celery_worker.rewrite_content_task": "rewrite-queue",
    "app.worker.celery_worker.retry_pending_llm_jobs_task": "rewrite-queue",
}

celery_app.conf.beat_schedule = {
    "send-weekly-reports": {
        "task": "app.worker.celery_worker.send_weekly_reports_task",
        "schedule": crontab(hour=9, minute=0, day_of_week=1),
    },
    "retry-pending-llm-jobs": {
        "task": "app.worker.celery_worker.retry_pending_llm_jobs_task",
        "schedule": crontab(minute="*/15"),
    },
}

celery_app.conf.update(task_track_started=True)
