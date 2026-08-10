from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from uuid import UUID

from app.db.session import get_db
from app.services import api_key_service
from app.core.security import get_current_user

router = APIRouter()


class APIKeyCreate(BaseModel):
    name: Optional[str] = None


class APIKeyInfo(BaseModel):
    id: UUID
    organization_id: UUID
    site_id: UUID
    prefix: str
    name: Optional[str]
    created_at: Optional[datetime] = None


class APIKeyResponse(BaseModel):
    plain_key: str
    key_info: dict


@router.post("/sites/{site_id}/keys", response_model=APIKeyResponse)
async def create_new_key(
    site_id: UUID,
    data: APIKeyCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Generate a new API key for the site."""
    # Derive organization_id from the site, not from client body (F6)
    org_id = api_key_service.get_site_organization_id(db, str(site_id))
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site not found",
        )

    try:
        plain_key, key_info = api_key_service.create_api_key(
            db,
            organization_id=org_id,
            site_id=str(site_id),
            name=data.name,
        )
        return {"plain_key": plain_key, "key_info": key_info}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create API key: {str(e)}",
        )


@router.get("/sites/{site_id}/keys")
async def get_keys(
    site_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List all active API keys for a site (hashes hidden)."""
    return api_key_service.list_api_keys(db, str(site_id))


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Revoke an API key (soft delete)."""
    revoked = api_key_service.revoke_api_key(db, str(key_id))
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or already revoked",
        )
    return None
