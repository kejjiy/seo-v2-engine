import secrets
import hashlib
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text

def generate_key_pair() -> Tuple[str, str, str]:
    """Generate a plain key, its hash, and a prefix."""
    plain_key = f"sv2_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
    prefix = plain_key[:8]
    return plain_key, key_hash, prefix

def create_api_key(
    db: Session,
    organization_id: str,
    site_id: str,
    name: Optional[str] = None
) -> Tuple[str, dict]:
    """Create a new API key in the database."""
    plain_key, key_hash, prefix = generate_key_pair()

    query = text("""
        INSERT INTO public.api_keys (organization_id, site_id, key_hash, prefix, name)
        VALUES (:org_id, :site_id, :key_hash, :prefix, :name)
        RETURNING id, organization_id, site_id, prefix, name, created_at
    """)

    result = db.execute(query, {
        "org_id": organization_id,
        "site_id": site_id,
        "key_hash": key_hash,
        "prefix": prefix,
        "name": name
    })
    db.commit()

    row = result.fetchone()
    # Convert Row to dict
    key_info = {
        "id": str(row[0]),
        "organization_id": str(row[1]),
        "site_id": str(row[2]),
        "prefix": row[3],
        "name": row[4],
        "created_at": row[5]
    }

    return plain_key, key_info

def list_api_keys(db: Session, site_id: str):
    """List active API keys for a specific site (without hashes)."""
    query = text("""
        SELECT id, organization_id, site_id, prefix, name, created_at
        FROM public.api_keys
        WHERE site_id = :site_id AND status = 'active'
    """)
    result = db.execute(query, {"site_id": site_id})
    return [
        {
            "id": str(row[0]),
            "organization_id": str(row[1]),
            "site_id": str(row[2]),
            "prefix": row[3],
            "name": row[4],
            "created_at": row[5]
        } for row in result.fetchall()
    ]

def revoke_api_key(db: Session, key_id: str) -> bool:
    """Revoke an API key (soft delete by setting status to 'revoked').

    Returns True if a key was revoked, False if key not found.
    """
    query = text("""
        UPDATE public.api_keys
        SET status = 'revoked'
        WHERE id = :key_id AND status = 'active'
    """)
    result = db.execute(query, {"key_id": key_id})
    db.commit()
    return result.rowcount > 0

def verify_api_key(db: Session, plain_key: str) -> Optional[dict]:
    """Verify an API key and return its info if valid and active."""
    key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
    query = text("""
        SELECT id, organization_id, site_id
        FROM public.api_keys
        WHERE key_hash = :key_hash AND status = 'active'
    """)
    row = db.execute(query, {"key_hash": key_hash}).fetchone()
    if row:
        return {
            "id": str(row[0]),
            "organization_id": str(row[1]),
            "site_id": str(row[2])
        }
    return None

def get_site_organization_id(db: Session, site_id: str) -> Optional[str]:
    """Look up the organization_id for a given site.

    Returns the organization_id string or None if site not found.
    """
    query = text("""
        SELECT organization_id FROM public.sites WHERE id = :site_id
    """)
    row = db.execute(query, {"site_id": site_id}).fetchone()
    if row:
        return str(row[0])
    return None
