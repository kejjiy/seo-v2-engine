from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import audit
from app.core.security import get_current_user
from app.db.session import get_db


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(audit.router, prefix="/api/v1")

    async def override_get_current_user():
        return {"token": "test-token", "user_id": "user-1"}

    def override_get_db():
        yield MagicMock()

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_launch_site_audit_crawl_queues_task():
    client = _build_client()
    mock_site = MagicMock(id="site-1", url="https://example.com")
    mock_tracker = MagicMock(id="audit-1")

    with (
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_accessible_site",
            return_value=mock_site,
        ),
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_latest_audit_tracker",
            return_value=None,
        ),
        patch(
            "app.api.v1.endpoints.audit.audit_service.create_audit_tracker",
            return_value=mock_tracker,
        ),
        patch("app.api.v1.endpoints.audit.run_site_audit_task.delay") as mock_delay,
    ):
        response = client.post(
            "/api/v1/sites/site-1/crawl", headers={"Authorization": "Bearer token"}
        )

    assert response.status_code == 200
    assert response.json()["audit_id"] == "audit-1"
    mock_delay.assert_called_once_with(
        "audit-1", "site-1", "https://example.com", "crawl"
    )


def test_launch_site_audit_rewrite_rejects_missing_pages():
    client = _build_client()
    mock_site = MagicMock(id="site-1", url="https://example.com")

    with (
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_accessible_site",
            return_value=mock_site,
        ),
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_latest_audit_tracker",
            return_value=None,
        ),
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_eligible_pages",
            return_value=[],
        ),
    ):
        response = client.post(
            "/api/v1/sites/site-1/rewrite", headers={"Authorization": "Bearer token"}
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "No eligible pages available for rewrite."


def test_launch_site_audit_blocks_parallel_run():
    client = _build_client()
    mock_site = MagicMock(id="site-1", url="https://example.com")
    active_tracker = MagicMock(rewrite_status="rewriting")

    with (
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_accessible_site",
            return_value=mock_site,
        ),
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_latest_audit_tracker",
            return_value=active_tracker,
        ),
    ):
        response = client.post(
            "/api/v1/sites/site-1/crawl", headers={"Authorization": "Bearer token"}
        )

    assert response.status_code == 409


def test_get_site_audit_status_returns_payload():
    client = _build_client()
    mock_site = MagicMock(id="site-1", url="https://example.com")
    mock_tracker = MagicMock(id="audit-1")
    payload = {
        "audit_id": "audit-1",
        "site_id": "site-1",
        "status": "rewriting",
        "current_stage": "rewriting",
        "progress_percent": 55,
        "message": "Rewriting eligible pages.",
        "stages": [{"name": "rewriting", "status": "active"}],
        "rewrite": {
            "eligible_pages": 4,
            "rewritten_pages": 2,
            "failed_pages": 0,
            "review_pages": 1,
        },
        "report": {"pdf_ready": False, "delivery": "dashboard_link"},
        "error_message": None,
    }

    with (
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_accessible_site",
            return_value=mock_site,
        ),
        patch(
            "app.api.v1.endpoints.audit.audit_service.get_latest_audit_tracker",
            return_value=mock_tracker,
        ),
        patch(
            "app.api.v1.endpoints.audit.audit_service.build_audit_status_payload",
            return_value=payload,
        ),
    ):
        response = client.get(
            "/api/v1/sites/site-1/audit-status",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "rewriting"
    assert response.json()["rewrite"]["eligible_pages"] == 4
