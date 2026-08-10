"""Tests for API Key Service - core business logic."""
import hashlib
import pytest
from unittest.mock import MagicMock, patch

from app.services.api_key_service import (
    generate_key_pair,
    create_api_key,
    list_api_keys,
    revoke_api_key,
    verify_api_key,
)


class TestGenerateKeyPair:
    """Tests for key generation logic."""

    def test_generates_plain_key_with_prefix(self):
        """Plain key should start with 'sv2_'."""
        plain_key, key_hash, prefix = generate_key_pair()
        assert plain_key.startswith("sv2_")

    def test_key_hash_matches_plain_key(self):
        """Hash should be SHA-256 of the plain key."""
        plain_key, key_hash, prefix = generate_key_pair()
        expected_hash = hashlib.sha256(plain_key.encode()).hexdigest()
        assert key_hash == expected_hash

    def test_prefix_is_first_8_chars(self):
        """Prefix should be the first 8 characters of the plain key."""
        plain_key, key_hash, prefix = generate_key_pair()
        assert prefix == plain_key[:8]

    def test_generates_unique_keys(self):
        """Two calls should produce different keys."""
        key1, _, _ = generate_key_pair()
        key2, _, _ = generate_key_pair()
        assert key1 != key2

    def test_key_has_sufficient_entropy(self):
        """Plain key should be at least 40 characters (sv2_ + 32-byte base64)."""
        plain_key, _, _ = generate_key_pair()
        assert len(plain_key) >= 40


class TestCreateAPIKey:
    """Tests for key creation in database."""

    def test_create_api_key_returns_plain_key_and_info(self):
        """Should return the plain key and key info dict."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (
            "uuid-1", "org-1", "site-1", "sv2_abcd", "Test Key", "2026-02-11T00:00:00Z"
        )
        mock_db.execute.return_value = mock_result

        plain_key, key_info = create_api_key(mock_db, "org-1", "site-1", name="Test Key")

        assert plain_key.startswith("sv2_")
        assert key_info["organization_id"] == "org-1"
        assert key_info["site_id"] == "site-1"
        assert key_info["name"] == "Test Key"
        mock_db.commit.assert_called_once()

    def test_create_api_key_stores_hash_not_plain(self):
        """The hash stored in DB should not be the plain key."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (
            "uuid-1", "org-1", "site-1", "sv2_abcd", None, "2026-02-11T00:00:00Z"
        )
        mock_db.execute.return_value = mock_result

        plain_key, _ = create_api_key(mock_db, "org-1", "site-1")

        # Verify the SQL was called with a hash, not the plain key
        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert params["key_hash"] != plain_key
        assert params["key_hash"] == hashlib.sha256(plain_key.encode()).hexdigest()


class TestListAPIKeys:
    """Tests for listing keys."""

    def test_list_returns_keys_without_hash(self):
        """Listed keys should NOT include the hash."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("uuid-1", "org-1", "site-1", "sv2_abcd", "Key 1", "2026-02-11T00:00:00Z"),
            ("uuid-2", "org-1", "site-1", "sv2_efgh", "Key 2", "2026-02-11T00:00:00Z"),
        ]
        mock_db.execute.return_value = mock_result

        keys = list_api_keys(mock_db, "site-1")

        assert len(keys) == 2
        assert keys[0]["prefix"] == "sv2_abcd"
        assert keys[1]["name"] == "Key 2"
        # Verify no hash field in response
        for key in keys:
            assert "key_hash" not in key

    def test_list_returns_empty_for_no_keys(self):
        """Should return empty list when site has no keys."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute.return_value = mock_result

        keys = list_api_keys(mock_db, "site-no-keys")
        assert keys == []


class TestRevokeAPIKey:
    """Tests for key revocation (soft delete)."""

    def test_revoke_existing_key_returns_true(self):
        """Revoking an existing active key should return True."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute.return_value = mock_result

        result = revoke_api_key(mock_db, "uuid-1")

        assert result is True
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_revoke_nonexistent_key_returns_false(self):
        """Revoking a non-existent or already revoked key should return False."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_db.execute.return_value = mock_result

        result = revoke_api_key(mock_db, "uuid-nonexistent")

        assert result is False
        mock_db.commit.assert_called_once()


class TestVerifyAPIKey:
    """Tests for key verification (used by middleware)."""

    def test_verify_valid_key_returns_info(self):
        """Valid key should return site info dict."""
        mock_db = MagicMock()
        plain_key = "sv2_test_key_123"
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()

        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("uuid-1", "org-1", "site-1")
        mock_db.execute.return_value = mock_result

        result = verify_api_key(mock_db, plain_key)

        assert result is not None
        assert result["organization_id"] == "org-1"
        assert result["site_id"] == "site-1"

    def test_verify_invalid_key_returns_none(self):
        """Invalid key should return None."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_db.execute.return_value = mock_result

        result = verify_api_key(mock_db, "sv2_invalid_key")
        assert result is None

    def test_verify_uses_hash_for_lookup(self):
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
