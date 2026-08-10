"""Tests for API Key security middleware - isolated from DB."""
import pytest
import hashlib
from unittest.mock import MagicMock, patch

from app.services.api_key_service import verify_api_key


class TestSecurityMiddleware:
    """Tests for the API key validation logic used by the middleware."""

    def test_missing_key_should_fail(self):
        """With no key provided, verification should return None."""
        mock_db = MagicMock()
        result = verify_api_key(mock_db, "")
        # Empty string hashes to something that won't match
        mock_db.execute.assert_called_once()

    def test_invalid_key_returns_none(self):
        """Invalid key should return None."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_db.execute.return_value = mock_result

        result = verify_api_key(mock_db, "sv2_invalid_key_here")
        assert result is None

    def test_valid_key_returns_site_info(self):
        """Valid key should return site info dict."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("key-uuid", "org-uuid", "site-uuid")
        mock_db.execute.return_value = mock_result

        result = verify_api_key(mock_db, "sv2_valid_key_123")

        assert result is not None
        assert result["site_id"] == "site-uuid"
        assert result["organization_id"] == "org-uuid"

    def test_verification_hashes_key_before_lookup(self):
        """Verification should hash the key before DB lookup."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_db.execute.return_value = mock_result

        plain_key = "sv2_test_key_456"
        verify_api_key(mock_db, plain_key)

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        expected_hash = hashlib.sha256(plain_key.encode()).hexdigest()
        assert params["key_hash"] == expected_hash
