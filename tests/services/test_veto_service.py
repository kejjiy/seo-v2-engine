from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import veto_service


def _result(row):
    mocked = MagicMock()
    mocked.fetchone.return_value = row
    return mocked


def test_hash_token_is_stable():
    assert veto_service.hash_token("abc") == veto_service.hash_token("abc")
    assert veto_service.hash_token("abc") != veto_service.hash_token("abcd")


def test_create_veto_event_stores_hashed_token_only():
    db = MagicMock()
    with patch(
        "app.services.veto_service.generate_veto_token", return_value="plain-token"
    ):
        token = veto_service.create_veto_event(
            db,
            organization_id="org-1",
            site_id="site-1",
            job_id="job-1",
            ttl_hours=48,
        )

    assert token == "plain-token"
    params = db.execute.call_args[0][1]
    assert params["organization_id"] == "org-1"
    assert params["site_id"] == "site-1"
    assert params["job_id"] == "job-1"
    assert params["token_hash"] == veto_service.hash_token("plain-token")
    db.commit.assert_called_once()


def test_confirm_veto_token_marks_confirmed_and_rejected():
    db = MagicMock()
    confirmed_row = SimpleNamespace(
        id="veto-1",
        organization_id="org-1",
        site_id="site-1",
        job_id="job-1",
    )
    db.execute.side_effect = [_result(confirmed_row), _result(None)]

    result = veto_service.confirm_veto_token(db, "token-1")

    assert result.state == "confirmed"
    assert result.veto_event_id == "veto-1"
    assert result.job_id == "job-1"
    assert db.commit.called


def test_confirm_veto_token_returns_invalid_for_unknown_token():
    db = MagicMock()
    db.execute.side_effect = [_result(None), _result(None)]

    result = veto_service.confirm_veto_token(db, "token-1")
    assert result.state == "invalid_or_expired"


def test_confirm_veto_token_returns_invalid_for_expired_pending_token():
    db = MagicMock()
    existing_row = SimpleNamespace(
        id="veto-1",
        organization_id="org-1",
        site_id="site-1",
        job_id="job-1",
        status="pending",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        failure_reason=None,
    )
    db.execute.side_effect = [_result(None), _result(existing_row)]

    result = veto_service.confirm_veto_token(db, "token-1")
    assert result.state == "invalid_or_expired"


def test_confirm_veto_token_returns_already_processed_when_confirmed():
    db = MagicMock()
    existing_row = SimpleNamespace(
        id="veto-1",
        organization_id="org-1",
        site_id="site-1",
        job_id="job-1",
        status="confirmed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        failure_reason=None,
    )
    db.execute.side_effect = [_result(None), _result(existing_row)]

    result = veto_service.confirm_veto_token(db, "token-1")
    assert result.state == "already_processed"


def test_confirm_veto_token_returns_rollback_failed_state():
    db = MagicMock()
    existing_row = SimpleNamespace(
        id="veto-1",
        organization_id="org-1",
        site_id="site-1",
        job_id="job-1",
        status="failed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        failure_reason="revision_unavailable",
    )
    db.execute.side_effect = [_result(None), _result(existing_row)]

    result = veto_service.confirm_veto_token(db, "token-1")
    assert result.state == "rollback_failed"
    assert result.failure_reason == "revision_unavailable"
