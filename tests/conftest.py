"""Shared test fixtures for the SEO-v2 Engine test suite.

Provides reusable mock objects and test clients to avoid
duplication across test files (F5).
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import keys, sync
from app.core.security import get_current_site, get_current_user
from app.db.session import get_db


@pytest.fixture
def mock_db():
    """Create a reusable mock database session."""
    return MagicMock()


@pytest.fixture
def test_client():
    """Create a FastAPI TestClient with all v1 routers and mocked dependencies.

    Uses a minimal FastAPI app (no Redis/Celery) to avoid external
    service dependencies in unit tests.
    """
    app = FastAPI()
    app.include_router(keys.router, prefix="/api/v1")
    app.include_router(sync.router, prefix="/api/v1")

    def override_get_db():
        yield MagicMock()

    async def override_get_current_user():
        return {"token": "test-token", "user_id": "test-user"}

    async def override_get_current_site():
        return {
            "site_id": "550e8400-e29b-41d4-a716-446655440001",
            "organization_id": "org-uuid",
        }

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_current_site] = override_get_current_site

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
