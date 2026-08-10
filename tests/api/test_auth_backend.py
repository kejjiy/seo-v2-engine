import base64
import hashlib
import hmac
import json
import sys
import time
import types
from unittest.mock import MagicMock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

fake_pdf_module = types.ModuleType("app.services.reporting.pdf_generator")


class _FakeBrandingConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakePDFGenerator:
    async def generate_pdf_async(self, *args, **kwargs):
        return b"%PDF-1.4"


fake_pdf_module.BrandingConfig = _FakeBrandingConfig
fake_pdf_module.PDFGenerator = _FakePDFGenerator
sys.modules.setdefault("app.services.reporting.pdf_generator", fake_pdf_module)

from app.api.v1.endpoints import keys
from app.core.security import get_current_user
from app.db.session import get_db


def _encode_json(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("utf-8")


def _make_hs256_token(secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": "user-123",
        "email": "user@example.com",
        "aud": "authenticated",
        "iss": "https://example.supabase.co/auth/v1",
        "exp": int(time.time()) + 3600,
    }
    header_b64 = _encode_json(header)
    payload_b64 = _encode_json(payload)
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("utf-8")
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(keys.router, prefix="/api/v1")

    @app.get("/api/v1/protected-check")
    async def protected_check(current_user: dict = Depends(get_current_user)):
        return {"user_id": current_user["user_id"]}

    def override_get_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_missing_bearer_is_rejected():
    client = _build_client()

    response = client.get("/api/v1/sites/550e8400-e29b-41d4-a716-446655440001/keys")

    assert response.status_code == 401
    assert response.json()["detail"] == "Bearer token missing"


def test_invalid_bearer_is_rejected():
    client = _build_client()
    with (
        patch("app.core.security.settings.SUPABASE_JWT_SECRET", "secret"),
        patch("app.core.security.settings.SUPABASE_JWT_AUDIENCE", "authenticated"),
        patch(
            "app.core.security.settings.SUPABASE_JWT_ISSUER",
            "https://example.supabase.co/auth/v1",
        ),
    ):
        response = client.get(
            "/api/v1/sites/550e8400-e29b-41d4-a716-446655440001/keys",
            headers={"Authorization": "Bearer invalid.token.value"},
        )

    assert response.status_code == 401


def test_valid_bearer_is_accepted_on_protected_routes():
    client = _build_client()
    token = _make_hs256_token("secret")

    with (
        patch("app.core.security.settings.SUPABASE_JWT_SECRET", "secret"),
        patch("app.core.security.settings.SUPABASE_JWT_AUDIENCE", "authenticated"),
        patch(
            "app.core.security.settings.SUPABASE_JWT_ISSUER",
            "https://example.supabase.co/auth/v1",
        ),
        patch("app.services.api_key_service.list_api_keys", return_value=[]),
    ):
        keys_response = client.get(
            "/api/v1/sites/550e8400-e29b-41d4-a716-446655440001/keys",
            headers={"Authorization": f"Bearer {token}"},
        )
        protected_response = client.get(
            "/api/v1/protected-check",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert keys_response.status_code == 200
    assert protected_response.status_code == 200
    assert protected_response.json()["user_id"] == "user-123"
