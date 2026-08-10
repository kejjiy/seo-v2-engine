from datetime import datetime, timedelta

import pytest

from app.services.reporting.aggregator import ReportAggregator, WeeklyReportData


def test_aggregator_gets_correct_stats():
    class MockSession:
        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            class MockJob:
                def __init__(self, action, ims, job_id):
                    self.id = job_id
                    self.action_taken = action
                    self.ims_improvement = ims
                    self.status = "completed"
                    self.decision_status = "applied"
                    self.rejection_source = None

            return [
                MockJob("Added H1", 5, "job-1"),
                MockJob("Optimized Title", 10, "job-2"),
            ]

    session = MockSession()
    aggregator = ReportAggregator(session)
    report: WeeklyReportData = aggregator.generate_report(site_id="test-site")

    assert report.total_fixes == 2
    assert report.win_of_the_week == "Optimized Title"
    assert report.zero_critical_failures is True
    assert len(report.change_rows) == 2
    assert report.change_rows[0].job_id == "job-1"
