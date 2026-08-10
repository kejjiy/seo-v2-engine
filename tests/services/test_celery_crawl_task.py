"""Tests for the Celery full crawl task (Story 3.4, Task 4).

Tests the run_full_crawl_task Celery worker function with mocked
crawler and database to verify:
- Task orchestration (crawl → persist → return result)
- Job status updates during execution
- Error handling (crawl failure, DB failure)
- Hard cap propagation
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from app.services.crawler.full import CrawlResult, PageResult


class TestRunFullCrawlTask:
    """Test the run_full_crawl_task Celery task."""

    def _make_crawl_result(
        self,
        site_id: str = "test-site",
        total_crawled: int = 3,
        total_discovered: int = 3,
        quota_reached: bool = False,
        pages: list = None,
        errors: list = None,
    ) -> CrawlResult:
        """Create a CrawlResult with sensible defaults."""
        if pages is None:
            pages = [
                PageResult(
                    url=f"https://example.com/page{i}",
                    status_code=200,
                    title=f"Page {i}",
                    h1_count=1,
                    html_size=1000,
                    raw_html=f"<html><body>Page {i}</body></html>",
                    crawled_at=datetime.now(timezone.utc),
                )
                for i in range(total_crawled)
            ]
        return CrawlResult(
            site_id=site_id,
            pages=pages,
            total_discovered=total_discovered,
            total_crawled=total_crawled,
            quota_reached=quota_reached,
            errors=errors or [],
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

    def _run_task(self, crawl_result, mock_db=None, crawl_exception=None, **task_args):
        """Helper to invoke the task with mocked dependencies.

        Since FullCrawler, SessionLocal, and Page are imported locally
        inside run_full_crawl_task, we patch them at their source modules
        and mock asyncio to skip the real event loop.
        """
        from app.worker.celery_worker import run_full_crawl_task

        defaults = {
            "site_id": "test-site",
            "start_url": "https://example.com",
            "hard_cap": 500,
        }
        defaults.update(task_args)

        with (
            patch("app.services.crawler.full.FullCrawler") as mock_crawler_cls,
            patch("app.db.session.SessionLocal") as mock_session_cls,
            patch("app.models.page.Page") as mock_page_cls,
        ):
            mock_crawler = MagicMock()
            mock_crawler_cls.return_value = mock_crawler

            if mock_db is not None:
                mock_session_cls.return_value = mock_db
                mock_db.__enter__.return_value = mock_db
                mock_db.__exit__.return_value = False
            else:
                db = MagicMock()
                db.__enter__.return_value = db
                db.__exit__.return_value = False
                db.query.return_value.filter.return_value.first.return_value = None
                mock_session_cls.return_value = db

            # Mock asyncio to avoid real event loops
            with (
                patch("asyncio.new_event_loop") as mock_new_loop,
                patch("asyncio.set_event_loop"),
                patch.object(run_full_crawl_task, "update_state") as mock_update,
            ):
                mock_loop = MagicMock()
                if crawl_exception:
                    mock_loop.run_until_complete.side_effect = crawl_exception
                else:
                    mock_loop.run_until_complete.return_value = crawl_result
                mock_new_loop.return_value = mock_loop

                # Use .run() which injects `self` for bound tasks
                result = run_full_crawl_task.run(
                    defaults["site_id"],
                    defaults["start_url"],
                    defaults["hard_cap"],
                )
                return result, mock_update, mock_session_cls

    def test_successful_crawl_and_persist(self):
        """Task should crawl, persist pages, and return a success summary."""
        crawl_result = self._make_crawl_result(total_crawled=2, total_discovered=2)
        result, _, _ = self._run_task(crawl_result)

        assert result["status"] == "completed"
        assert result["site_id"] == "test-site"
        assert result["total_crawled"] == 2
        assert result["pages_persisted"] == 2
        assert result["quota_reached"] is False

    def test_crawl_with_quota_reached(self):
        """Task should report quota_reached when hard cap is hit."""
        crawl_result = self._make_crawl_result(
            total_crawled=5, total_discovered=10, quota_reached=True
        )
        result, _, _ = self._run_task(crawl_result, hard_cap=5)

        assert result["quota_reached"] is True
        assert result["total_crawled"] == 5

    def test_crawl_failure_returns_failed_status(self):
        """Task should return 'failed' status when crawl raises."""
        result, _, _ = self._run_task(
            crawl_result=None,
            crawl_exception=RuntimeError("Connection refused"),
        )

        assert result["status"] == "failed"
        assert "Connection refused" in result["error"]

    def test_db_error_during_persist(self):
        """Task should handle DB errors gracefully."""
        crawl_result = self._make_crawl_result(total_crawled=1, total_discovered=1)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.commit.side_effect = Exception("DB constraint violation")

        result, _, _ = self._run_task(crawl_result, mock_db=mock_db)

        assert result["status"] == "completed"
        assert result["db_errors"] == 1
        assert result["pages_persisted"] == 0

    def test_task_updates_state(self):
        """Task should update Celery state to PROCESSING during execution."""
        crawl_result = self._make_crawl_result(
            total_crawled=0, total_discovered=0, pages=[]
        )
        result, mock_update, _ = self._run_task(crawl_result)

        mock_update.assert_called_once_with(
            state="PROCESSING",
            meta={"site_id": "test-site", "status": "crawling", "pages_crawled": 0},
        )
        assert result["status"] == "completed"

    def test_upsert_updates_existing_page(self):
        """When a page exists, the task should update it (not insert)."""
        crawl_result = self._make_crawl_result(total_crawled=1, total_discovered=1)

        existing_page = MagicMock()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = (
            existing_page
        )

        result, _, _ = self._run_task(crawl_result, mock_db=mock_db)

        assert result["pages_persisted"] == 1
        mock_db.add.assert_not_called()
        assert existing_page.title == crawl_result.pages[0].title
        assert existing_page.raw_html == crawl_result.pages[0].raw_html
