from unittest.mock import MagicMock, patch

from app.worker.celery_worker import process_sync_batch_task


def _make_batch(post_id=1):
    return [
        {
            "post_id": post_id,
            "title": "Title",
            "content": "Body",
            "url": "https://example.com/page",
            "date_modified": "2026-03-08T10:00:00Z",
        }
    ]


def test_process_sync_batch_skips_rejected_changes():
    db = MagicMock()
    db.__enter__.return_value = db
    db.__exit__.return_value = False
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = MagicMock()

    with patch("app.db.session.SessionLocal", return_value=db):
        result = process_sync_batch_task.run("site-1", _make_batch())

    assert result["status"] == "processed"
    assert result["processed_count"] == 0
    assert result["skipped_count"] == 1


def test_process_sync_batch_processes_when_not_rejected():
    db = MagicMock()
    db.__enter__.return_value = db
    db.__exit__.return_value = False
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    db.query.return_value.filter.return_value.first.return_value = None

    with patch("app.db.session.SessionLocal", return_value=db):
        result = process_sync_batch_task.run("site-1", _make_batch())

    assert result["status"] == "processed"
    assert result["processed_count"] == 1
    assert result["skipped_count"] == 0
    assert db.add.call_args[0][0].raw_html == "Body"


def test_process_sync_batch_falls_back_when_db_guard_unavailable():
    with patch("app.db.session.SessionLocal", side_effect=RuntimeError("db down")):
        result = process_sync_batch_task.run("site-1", _make_batch())

    assert result["status"] == "processed"
    assert result["processed_count"] == 0
    assert result["error_count"] == 1
