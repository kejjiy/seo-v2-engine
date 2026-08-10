from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi_limiter import FastAPILimiter
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import veto as veto_module
from app.core.security import get_current_site
from app.db.session import get_db
from app.services.veto_service import VetoConfirmResult


class FakeRedis:
    def __init__(self):
        self._counter = {}

    async def script_load(self, script):
        return "fake-sha"

    async def evalsha(self, sha, numkeys, *keys_and_args):
        key = keys_and_args[0]
        limit = int(keys_and_args[numkeys])
        self._counter[key] = self._counter.get(key, 0) + 1
        if self._counter[key] > limit:
            return 60000
        return 0

    async def close(self):
        return None


@pytest_asyncio.fixture
async def veto_client():
    app = FastAPI()
    app.include_router(veto_module.router, prefix="/api/v1")

    def override_get_db():
        yield MagicMock()

    async def override_get_current_site():
        return {"site_id": "site-1", "organization_id": "org-1"}

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_site] = override_get_current_site

    fake_redis = FakeRedis()
    await FastAPILimiter.init(fake_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await fake_redis.close()
    FastAPILimiter.redis = None


@pytest.mark.asyncio
async def test_confirm_veto_queues_rollback_when_confirmed(veto_client):
    with (
        patch(
            "app.api.v1.endpoints.veto.veto_service.confirm_veto_token",
            return_value=VetoConfirmResult(
                state="confirmed",
                message="ok",
                veto_event_id="veto-1",
            ),
        ),
        patch(
            "app.api.v1.endpoints.veto.process_veto_rollback_task.delay"
        ) as mock_delay,
    ):
        response = await veto_client.post(
            "/api/v1/veto/confirm",
            json={"token": "x" * 32},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "confirmed"
    mock_delay.assert_called_once_with(veto_event_id="veto-1")


@pytest.mark.asyncio
async def test_confirm_veto_does_not_queue_for_invalid_token(veto_client):
    with (
        patch(
            "app.api.v1.endpoints.veto.veto_service.confirm_veto_token",
            return_value=VetoConfirmResult(
                state="invalid_or_expired",
                message="expired",
            ),
        ),
        patch(
            "app.api.v1.endpoints.veto.process_veto_rollback_task.delay"
        ) as mock_delay,
    ):
        response = await veto_client.post(
            "/api/v1/veto/confirm",
            json={"token": "x" * 32},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "invalid_or_expired"
    mock_delay.assert_not_called()


@pytest.mark.asyncio
async def test_register_callback_secret_uses_site_scope(veto_client):
    with patch(
        "app.api.v1.endpoints.veto.veto_service.register_callback_secret_hash"
    ) as mock_register:
        response = await veto_client.post(
            "/api/v1/veto/callback-secret",
            json={"secret_hash": "a" * 64},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "registered"
    kwargs = mock_register.call_args.kwargs
    assert kwargs["site_id"] == "site-1"
    assert kwargs["organization_id"] == "org-1"
