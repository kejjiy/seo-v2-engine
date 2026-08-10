from datetime import datetime, timedelta, timezone
from typing import List, Optional
from functools import lru_cache

from pydantic import BaseModel, Field

from app.models.job import Job


class ReportChangeRow(BaseModel):
    job_id: str
    action_taken: str
    decision_status: str = "applied"
    rejection_source: Optional[str] = None


class WeeklyReportData(BaseModel):
    site_id: str
    total_fixes: int
    zero_critical_failures: bool
    win_of_the_week: Optional[str] = None
    win_original: Optional[str] = None
    win_new: Optional[str] = None
    fixes_list: List[str] = Field(default_factory=list)
    change_rows: List[ReportChangeRow] = Field(default_factory=list)


class ReportPeriod(BaseModel):
    start_date: str
    end_date: str


class IMSTrendPoint(BaseModel):
    date: str
    score: int
    change: int = 0


class PageFixed(BaseModel):
    url: str
    fixed_at: str
    original_snippet: Optional[str] = None
    new_snippet: Optional[str] = None


class IssuesPrevented(BaseModel):
    critical: int = 0
    warning: int = 0
    info: int = 0
    total: int = 0


class TopImprovement(BaseModel):
    title: str
    impact: int


class PDFReportData(BaseModel):
    site_id: str
    site_name: str
    report_period: ReportPeriod
    ims_trend: List[IMSTrendPoint] = Field(default_factory=list)
    pages_fixed: List[PageFixed] = Field(default_factory=list)
    issues_prevented: IssuesPrevented = Field(default_factory=IssuesPrevented)
    top_improvements: List[TopImprovement] = Field(default_factory=list)
    site_health_score: int = 0


class ReportAggregator:
    def __init__(self, db_session):
        self.db = db_session

    def generate_report(self, site_id: str) -> WeeklyReportData:
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        jobs = self._get_report_jobs(site_id, seven_days_ago)

        total_fixes = len(jobs)
        zero_critical_failures = (
            all(job.status != "failed" for job in jobs) if jobs else True
        )

        win_of_the_week = None
        win_original = None
        win_new = None
        max_ims = -1
        fixes_list = []
        change_rows: List[ReportChangeRow] = []
        for job in jobs:
            if job.action_taken:
                decision_status = (
                    getattr(job, "decision_status", "applied") or "applied"
                )
                rejection_source = getattr(job, "rejection_source", None)
                action_line = job.action_taken
                if decision_status == "rejected":
                    action_line = f"{job.action_taken} (rejected)"
                    if rejection_source:
                        action_line = f"{action_line} - {rejection_source}"
                fixes_list.append(action_line)
                change_rows.append(
                    ReportChangeRow(
                        job_id=str(getattr(job, "id", "")),
                        action_taken=job.action_taken,
                        decision_status=decision_status,
                        rejection_source=rejection_source,
                    )
                )
            if job.ims_improvement and job.ims_improvement > max_ims:
                max_ims = job.ims_improvement
                win_of_the_week = job.action_taken
                win_original = getattr(job, "original_snippet", None)
                win_new = getattr(job, "new_snippet", None)

        if not win_of_the_week and fixes_list:
            win_of_the_week = fixes_list[0]

        return WeeklyReportData(
            site_id=site_id,
            total_fixes=total_fixes,
            zero_critical_failures=zero_critical_failures,
            win_of_the_week=win_of_the_week,
            win_original=win_original,
            win_new=win_new,
            fixes_list=fixes_list,
            change_rows=change_rows,
        )

    def _get_report_jobs(self, site_id: str, start_date: datetime) -> List[Job]:
        return (
            self.db.query(Job)
            .filter(
                Job.site_id == site_id,
                Job.created_at >= start_date,
                Job.page_id.is_not(None),
            )
            .all()
        )

    def generate_pdf_report_data(
        self, site_id: str, site_name: str, period_days: int = 30
    ) -> PDFReportData:
        """Generate comprehensive PDF report data for a site.

        Args:
            site_id: UUID of the site.
            site_name: Display name of the site.
            period_days: Number of days to include in the report.

        Returns:
            PDFReportData with all metrics for PDF generation.
        """
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=period_days)

        jobs = self._get_report_jobs(site_id, start_date)
        jobs.sort(key=lambda job: job.created_at, reverse=True)

        ims_trend = self._calculate_ims_trend(jobs, start_date, end_date)
        pages_fixed = self._extract_pages_fixed(jobs)
        issues_prevented = self._count_issues_prevented(jobs)
        top_improvements = self._rank_top_improvements(jobs)
        site_health_score = self._calculate_health_score(ims_trend, jobs)

        return PDFReportData(
            site_id=site_id,
            site_name=site_name,
            report_period=ReportPeriod(
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
            ),
            ims_trend=ims_trend,
            pages_fixed=pages_fixed,
            issues_prevented=issues_prevented,
            top_improvements=top_improvements,
            site_health_score=site_health_score,
        )

    def _calculate_ims_trend(
        self, jobs: List[Job], start_date: datetime, end_date: datetime
    ) -> List[IMSTrendPoint]:
        """Calculate daily IMS scores from jobs."""
        trend = []
        current_date = start_date
        prev_score = 0

        while current_date <= end_date:
            day_start = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)

            day_jobs = [
                j
                for j in jobs
                if day_start <= j.created_at < day_end and j.ims_improvement
            ]

            if day_jobs:
                score = sum(j.ims_improvement or 0 for j in day_jobs)
            else:
                score = prev_score

            change = score - prev_score if trend else 0
            trend.append(
                IMSTrendPoint(
                    date=current_date.strftime("%Y-%m-%d"),
                    score=score,
                    change=change,
                )
            )
            prev_score = score
            current_date += timedelta(days=1)

        return trend

    def _extract_pages_fixed(self, jobs: List[Job]) -> List[PageFixed]:
        """Extract pages fixed from jobs with before/after snippets."""
        pages = []
        seen_urls = set()

        for job in jobs:
            if job.action_taken and job.decision_status == "applied":
                url_key = getattr(job, "page_id", None) or str(job.id)
                if url_key not in seen_urls:
                    pages.append(
                        PageFixed(
                            url=f"Page #{job.page_id}" if job.page_id else "Unknown",
                            fixed_at=job.created_at.strftime("%Y-%m-%d %H:%M"),
                            original_snippet=getattr(job, "original_snippet", None),
                            new_snippet=getattr(job, "new_snippet", None),
                        )
                    )
                    seen_urls.add(url_key)

        return pages[:20]

    def _count_issues_prevented(self, jobs: List[Job]) -> IssuesPrevented:
        """Count issues prevented by job status and type."""
        critical = 0
        warning = 0
        info = 0

        for job in jobs:
            if job.status == "failed":
                critical += 1
            elif job.decision_status == "rejected":
                warning += 1
            elif job.decision_status == "applied":
                info += 1

        return IssuesPrevented(
            critical=critical,
            warning=warning,
            info=info,
            total=critical + warning + info,
        )

    def _rank_top_improvements(self, jobs: List[Job]) -> List[TopImprovement]:
        """Rank improvements by IMS impact."""
        improvements = []

        for job in jobs:
            if (
                job.ims_improvement
                and job.ims_improvement > 0
                and job.action_taken
                and job.decision_status == "applied"
            ):
                improvements.append(
                    TopImprovement(
                        title=job.action_taken[:100],
                        impact=job.ims_improvement,
                    )
                )

        improvements.sort(key=lambda x: x.impact, reverse=True)
        return improvements[:10]

    def _calculate_health_score(
        self, ims_trend: List[IMSTrendPoint], jobs: List[Job]
    ) -> int:
        """Calculate overall site health score (0-100)."""
        if not ims_trend:
            return 0

        latest_score = ims_trend[-1].score if ims_trend else 0
        failed_jobs = sum(1 for j in jobs if j.status == "failed")
        total_jobs = len(jobs) or 1

        failure_penalty = (failed_jobs / total_jobs) * 20
        base_score = min(latest_score, 100)

        health_score = max(0, int(base_score - failure_penalty))
        return health_score
