from unittest.mock import MagicMock, patch

import pytest

from app.worker.celery_worker import send_weekly_reports_task


def test_send_weekly_reports_task_executes_successfully():
    with (
        patch("app.db.session.SessionLocal") as mock_session_class,
        patch(
            "app.worker.celery_worker.send_single_report_email_task.delay"
        ) as mock_delay,
        patch("app.services.reporting.aggregator.ReportAggregator") as mock_agg_class,
        patch(
            "app.services.veto_service.create_veto_event", return_value="token-1"
        ) as mock_create_veto,
    ):
        mock_db = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_db

        mock_site = MagicMock()
        mock_site.id = "site-uuid"
        mock_site.organization_id = "org-uuid"

        # When querying active_sites, mock execute
        mock_db.execute.return_value.fetchall.side_effect = [
            [(mock_site.id, mock_site.organization_id)],  # Active sites
            [("test@example.com",)],  # Users for site
        ]

        mock_agg_inst = mock_agg_class.return_value
        report_data = MagicMock()
        change = MagicMock()
        change.job_id = "job-uuid"
        change.action_taken = "Added H1"
        change.decision_status = "applied"
        report_data.change_rows = [change]
        mock_agg_inst.generate_report.return_value = report_data

        result = send_weekly_reports_task()

        assert result["status"] == "completed"
        assert result["dispatched_count"] == 1
        mock_delay.assert_called_once()
        mock_create_veto.assert_called_once()


def test_send_weekly_reports_task_selects_trial_and_active_orgs():
    with (
        patch("app.db.session.SessionLocal") as mock_session_class,
        patch("app.services.reporting.aggregator.ReportAggregator") as mock_agg_class,
    ):
        mock_db = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_db
        mock_db.execute.return_value.fetchall.side_effect = [[], []]
        mock_agg_class.return_value.generate_report.return_value = MagicMock(
            change_rows=[]
        )

        send_weekly_reports_task()

        site_query = str(mock_db.execute.call_args_list[0][0][0])
        assert "subscription_status IN ('active', 'trial')" in site_query
