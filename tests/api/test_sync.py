import pytest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from app.api.v1.endpoints import sync as sync_module
from app.core.security import get_current_site


class TestSyncEndpoint:
    """Tests for POST /api/v1/sync."""

    @patch("app.api.v1.endpoints.sync.process_sync_batch_task.delay")
    def test_sync_accepts_valid_payload(self, mock_delay, test_client):
        """Should return 202 Accepted when payload is valid and authenticated.

        Auth is satisfied by the conftest override of get_current_site.
        """
        valid_payload = [
            {
                "post_id": 1,
                "title": "Hello World",
                "content": "<p>Welcome to WP</p>",
                "url": "https://example.com/hello-world",
                "date_modified": "2026-02-12T10:00:00Z",
            }
        ]

        response = test_client.post(
            "/api/v1/sync",
            json=valid_payload,
            headers={"X-API-Key": "sv2_test_valid_key"},
        )

        assert response.status_code == 202
        data = response.json()
        assert data["message"] == "Sync batch accepted"
        assert data["queued_items"] == 1

        # Verify celery task was called with correct site_id
        mock_delay.assert_called_once()
        call_kwargs = mock_delay.call_args
        assert call_kwargs.kwargs["site_id"] == "550e8400-e29b-41d4-a716-446655440001"

    def test_sync_rejects_missing_auth(self):
        """Should return 401 when X-API-Key is missing.

        Uses a dedicated app WITHOUT the get_current_site override
        so the real dependency raises HTTPException.
        """
        app = FastAPI()
        app.include_router(sync_module.router, prefix="/api/v1")
        # No dependency override → real get_current_site will execute
        # and raise 401 because there is no X-API-Key header.

        with TestClient(app, raise_server_exceptions=False) as client:
            valid_payload = [
                {
                    "post_id": 1,
                    "title": "Hello World",
                    "content": "<p>Welcome to WP</p>",
                    "url": "https://example.com/hello-world",
                    "date_modified": "2026-02-12T10:00:00Z",
                }
            ]

            response = client.post("/api/v1/sync", json=valid_payload)

        assert response.status_code in [401, 403]

    @patch("app.api.v1.endpoints.sync.process_sync_batch_task.delay")
    def test_sync_rejects_invalid_payload(self, mock_delay, test_client):
        """Should return 422 Unprocessable Entity when payload is invalid."""
        invalid_payload = [
            {
                "post_id": "not an int",
                "title": "Hello World",
                # Missing other required fields
            }
        ]

        response = test_client.post(
            "/api/v1/sync",
            json=invalid_payload,
            headers={"X-API-Key": "sv2_test_valid_key"},
        )

        assert response.status_code == 422
        # Celery task should NOT have been called on validation failure
        mock_delay.assert_not_called()
