from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from celery.exceptions import MaxRetriesExceededError

from app.worker.celery_worker import process_veto_rollback_task


def _db_with_row(row):
    db = MagicMock()
    execute_result = MagicMock()
    execute_result.fetchone.return_value = row
    db.execute.return_value = execute_result
    db.__enter__.return_value = db
    db.__exit__.return_value = False
    return db


def test_process_veto_rollback_task_marks_rolled_back_on_success():
    row = SimpleNamespace(
        id="veto-1",
        status="confirmed",
        job_id="job-1",
        site_id="site-1",
        url="https://example.com",
        veto_callback_secret_hash="abc123",
        page_id=42,
    )
    db = _db_with_row(row)

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"status": "rolled_back"}

    with (
        patch("app.db.session.SessionLocal", return_value=db),
        patch("httpx.post", return_value=response),
        patch("app.services.veto_service.mark_veto_event_result") as mock_mark,
    ):
        result = process_veto_rollback_task.run("veto-1")

    assert result["status"] == "rolled_back"
    mock_mark.assert_called_with(db, veto_event_id="veto-1", status="rolled_back")


def test_process_veto_rollback_task_marks_failed_when_revision_missing():
    row = SimpleNamespace(
        id="veto-1",
        status="confirmed",
        job_id="job-1",
        site_id="site-1",
        url="https://example.com",
        veto_callback_secret_hash="abc123",
        page_id=42,
    )
    db = _db_with_row(row)

    response = MagicMock()
    response.status_code = 409
    response.json.return_value = {
        "status": "failed",
        "error_code": "revision_unavailable",
    }

    with (
        patch("app.db.session.SessionLocal", return_value=db),
        patch("httpx.post", return_value=response),
        patch("app.services.veto_service.mark_veto_event_result") as mock_mark_veto,
        patch(
            "app.services.veto_service.mark_job_rejection_failure_reason"
        ) as mock_mark_job,
    ):
        result = process_veto_rollback_task.run("veto-1")

    assert result["status"] == "failed"
    assert result["reason"] == "revision_unavailable"
    mock_mark_veto.assert_called()
    mock_mark_job.assert_called_with(
        db,
        job_id="job-1",
        failure_reason="revision_unavailable",
    )


def test_process_veto_rollback_task_returns_temporary_error_after_retries():
    row = SimpleNamespace(
        id="veto-1",
        status="confirmed",
        job_id="job-1",
        site_id="site-1",
        url="https://example.com",
        veto_callback_secret_hash="abc123",
        page_id=42,
    )
    db = _db_with_row(row)

    with (
        patch("app.db.session.SessionLocal", return_value=db),
        patch("httpx.post", side_effect=httpx.ConnectError("network down")),
        patch.object(
            process_veto_rollback_task,
            "retry",
            side_effect=MaxRetriesExceededError("max retries reached"),
        ),
        patch("app.services.veto_service.mark_veto_event_result") as mock_mark_veto,
    ):
        result = process_veto_rollback_task.run("veto-1")

    assert result["status"] == "failed"
    assert result["reason"] == "temporary_error"
    mock_mark_veto.assert_called()
