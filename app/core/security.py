import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import Security, HTTPException, status, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services import api_key_service
from app.core.config import settings

api_key_header = APIKeyHeader(name=settings.API_KEY_NAME, auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)
_jwks_cache: dict[str, Any] = {"expires_at": 0.0, "keys": {}}


def _decode_base64url(value: str) -> bytes:
    padding_len = (-len(value)) % 4
    return base64.urlsafe_b64decode(value + ("=" * padding_len))


def _decode_token_parts(
    token: str,
) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise ValueError("Malformed JWT") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    header = json.loads(_decode_base64url(header_b64))
    payload = json.loads(_decode_base64url(payload_b64))
    signature = _decode_base64url(signature_b64)
    return header, payload, signing_input, signature


def _verify_time_claims(payload: dict[str, Any]) -> None:
    now = int(time.time())
    exp = payload.get("exp")
    nbf = payload.get("nbf")

    if exp is not None and now >= int(exp):
        raise ValueError("Token expired")
    if nbf is not None and now < int(nbf):
        raise ValueError("Token not yet valid")


def _verify_standard_claims(payload: dict[str, Any]) -> None:
    audience = settings.SUPABASE_JWT_AUDIENCE
    issuer = settings.SUPABASE_JWT_ISSUER

    if audience:
        token_aud = payload.get("aud")
        allowed = token_aud if isinstance(token_aud, list) else [token_aud]
        if audience not in allowed:
            raise ValueError("Invalid token audience")

    if issuer and payload.get("iss") != issuer:
        raise ValueError("Invalid token issuer")


def _verify_hs256(token: str) -> dict[str, Any]:
    if not settings.SUPABASE_JWT_SECRET:
        raise ValueError("SUPABASE_JWT_SECRET is not configured")

    header, payload, signing_input, signature = _decode_token_parts(token)
    if header.get("alg") != "HS256":
        raise ValueError("Unsupported JWT algorithm")

    expected = hmac.new(
        settings.SUPABASE_JWT_SECRET.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid token signature")

    _verify_time_claims(payload)
    _verify_standard_claims(payload)
    return payload


def _get_jwks_keys() -> dict[str, Any]:
    jwks_url = settings.SUPABASE_JWKS_URL
    if not jwks_url:
        raise ValueError("SUPABASE_JWKS_URL is not configured")

    now = time.time()
    if _jwks_cache["keys"] and now < float(_jwks_cache["expires_at"]):
        return _jwks_cache["keys"]

    response = httpx.get(jwks_url, timeout=5.0)
    response.raise_for_status()
    keys = {
        key["kid"]: key for key in response.json().get("keys", []) if key.get("kid")
    }
    _jwks_cache["keys"] = keys
    _jwks_cache["expires_at"] = now + 300
    return keys


def _rsa_public_key_from_jwk(jwk: dict[str, Any]) -> rsa.RSAPublicKey:
    modulus = int.from_bytes(_decode_base64url(jwk["n"]), byteorder="big")
    exponent = int.from_bytes(_decode_base64url(jwk["e"]), byteorder="big")
    return rsa.RSAPublicNumbers(exponent, modulus).public_key()


def _verify_rs256(token: str) -> dict[str, Any]:
    header, payload, signing_input, signature = _decode_token_parts(token)
    if header.get("alg") != "RS256":
        raise ValueError("Unsupported JWT algorithm")

    kid = header.get("kid")
    if not kid:
        raise ValueError("Missing JWT key id")

    jwk = _get_jwks_keys().get(kid)
    if not jwk:
        raise ValueError("Unknown JWT signing key")

    public_key = _rsa_public_key_from_jwk(jwk)
    public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())

    _verify_time_claims(payload)
    _verify_standard_claims(payload)
    return payload


def verify_supabase_jwt(token: str) -> dict[str, Any]:
    header, _, _, _ = _decode_token_parts(token)
    algorithm = header.get("alg")

    if algorithm == "HS256":
        return _verify_hs256(token)
    if algorithm == "RS256":
        return _verify_rs256(token)

    raise ValueError("Unsupported JWT algorithm")


def _build_user_payload(claims: dict[str, Any], token: str) -> dict[str, Any]:
    user_id = claims.get("sub") or claims.get("user_id")
    return {
        "token": token,
        "user_id": user_id,
        "email": claims.get("email"),
        "claims": claims,
    }


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
) -> dict:
    """Validate Bearer token and return user information."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_supabase_jwt(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return _build_user_payload(claims, credentials.credentials)


async def get_current_site(
    api_key: str = Security(api_key_header), db: Session = Depends(get_db)
) -> dict:
    """Validate API key and return site information."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key missing",
        )

    site_info = api_key_service.verify_api_key(db, api_key)
    if not site_info:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API Key",
        )

    return site_info
