"""Tests for API Key endpoints using FastAPI TestClient (F4 fix).

These are REAL endpoint tests that exercise the full HTTP stack:
FastAPI routing, Pydantic validation, serialization, and status codes.
"""
import pytest
from unittest.mock import patch


class TestCreateKeyEndpoint:
    """Tests for POST /api/v1/sites/{site_id}/keys."""

    @patch("app.services.api_key_service.create_api_key")
    @patch("app.services.api_key_service.get_site_organization_id")
    def test_create_key_returns_plain_key(self, mock_get_org, mock_create, test_client):
        """Should return the plain key and key info on creation."""
        mock_get_org.return_value = "org-uuid"
        mock_create.return_value = (
            "sv2_test_plain_key_abc123",
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "organization_id": "org-uuid",
                "site_id": "550e8400-e29b-41d4-a716-446655440001",
                "prefix": "sv2_test",
                "name": "My Key",
                "created_at": "2026-02-11T00:00:00Z",
            },
        )

        response = test_client.post(
            "/api/v1/sites/550e8400-e29b-41d4-a716-446655440001/keys",
            json={"name": "My Key"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["plain_key"] == "sv2_test_plain_key_abc123"
        assert data["key_info"]["prefix"] == "sv2_test"
        assert data["key_info"]["name"] == "My Key"

    @patch("app.services.api_key_service.get_site_organization_id")
    def test_create_key_site_not_found_returns_404(self, mock_get_org, test_client):
        """Should return 404 when site does not exist."""
        mock_get_org.return_value = None

        response = test_client.post(
            "/api/v1/sites/550e8400-e29b-41d4-a716-446655440099/keys",
            json={"name": "My Key"},
        )

        assert response.status_code == 404

    @patch("app.services.api_key_service.create_api_key")
    @patch("app.services.api_key_service.get_site_organization_id")
    def test_create_key_without_name(self, mock_get_org, mock_create, test_client):
        """Should allow creating key without a name."""
        mock_get_org.return_value = "org-uuid"
        mock_create.return_value = (
            "sv2_another_key_xyz",
            {
                "id": "550e8400-e29b-41d4-a716-446655440002",
                "organization_id": "org-uuid",
                "site_id": "550e8400-e29b-41d4-a716-446655440001",
                "prefix": "sv2_anot",
                "name": None,
                "created_at": "2026-02-11T00:00:00Z",
            },
        )

        response = test_client.post(
            "/api/v1/sites/550e8400-e29b-41d4-a716-446655440001/keys",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["key_info"]["name"] is None

    def test_create_key_invalid_uuid_returns_422(self, test_client):
        """Should return 422 for invalid UUID in path."""
        response = test_client.post(
            "/api/v1/sites/not-a-uuid/keys",
            json={"name": "Test"},
        )

        assert response.status_code == 422


class TestListKeysEndpoint:
    """Tests for GET /api/v1/sites/{site_id}/keys."""

    @patch("app.services.api_key_service.list_api_keys")
    def test_list_keys_returns_array(self, mock_list, test_client):
        """Should return a list of key info objects."""
        mock_list.return_value = [
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "organization_id": "org-uuid",
                "site_id": "site-uuid",
                "prefix": "sv2_test",
                "name": "Key 1",
                "created_at": "2026-02-11T00:00:00Z",
            }
        ]

        response = test_client.get(
            "/api/v1/sites/550e8400-e29b-41d4-a716-446655440001/keys"
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["prefix"] == "sv2_test"

    @patch("app.services.api_key_service.list_api_keys")
    def test_list_keys_empty(self, mock_list, test_client):
        """Should return empty list when no keys exist."""
        mock_list.return_value = []

        response = test_client.get(
            "/api/v1/sites/550e8400-e29b-41d4-a716-446655440001/keys"
        )

        assert response.status_code == 200
        assert response.json() == []


class TestRevokeKeyEndpoint:
    """Tests for DELETE /api/v1/keys/{key_id}."""

    @patch("app.services.api_key_service.revoke_api_key")
    def test_revoke_returns_204(self, mock_revoke, test_client):
        """Revoking a key should return 204 No Content."""
        mock_revoke.return_value = True

        response = test_client.delete(
            "/api/v1/keys/550e8400-e29b-41d4-a716-446655440000"
        )

        assert response.status_code == 204

    @patch("app.services.api_key_service.revoke_api_key")
    def test_revoke_nonexistent_returns_404(self, mock_revoke, test_client):
        """Revoking a non-existent key should return 404."""
        mock_revoke.return_value = False

        response = test_client.delete(
            "/api/v1/keys/550e8400-e29b-41d4-a716-446655440099"
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
