"""Business logic for one-click veto token lifecycle."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class VetoConfirmResult:
    """Canonical veto confirmation result."""

    state: str
    message: str
    veto_event_id: Optional[str] = None
    site_id: Optional[str] = None
    job_id: Optional[str] = None
    organization_id: Optional[str] = None
    failure_reason: Optional[str] = None


def hash_token(token: str) -> str:
    """Return SHA-256 hash for storage/lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_veto_token() -> str:
    """Generate a cryptographically strong token."""
    return secrets.token_urlsafe(32)


def create_veto_event(
    db: Session,
    *,
    organization_id: str,
    site_id: str,
    job_id: str,
    ttl_hours: int,
) -> str:
    """Create a pending veto event and return the plain token."""
    token = generate_veto_token()
    token_hash = hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

    db.execute(
        text(
            """
            INSERT INTO public.veto_events (
                organization_id,
                site_id,
                job_id,
                token_hash,
                status,
                expires_at
            )
            VALUES (
                :organization_id,
                :site_id,
                :job_id,
                :token_hash,
                'pending',
                :expires_at
            )
            """
        ),
        {
            "organization_id": organization_id,
            "site_id": site_id,
            "job_id": job_id,
            "token_hash": token_hash,
            "expires_at": expires_at,
        },
    )
    db.commit()
    return token


def build_veto_url(base_url: str, token: str) -> str:
    """Build the public dashboard URL used in weekly report emails."""
    normalized = base_url.rstrip("/")
    return f"{normalized}/veto?token={token}"


def confirm_veto_token(db: Session, token: str) -> VetoConfirmResult:
    """Confirm one-time token atomically and mark related job as rejected."""
    token_hash = hash_token(token)
    confirmed = db.execute(
        text(
            """
            UPDATE public.veto_events
            SET status = 'confirmed',
                confirmed_at = timezone('utc'::text, now())
            WHERE token_hash = :token_hash
              AND status = 'pending'
              AND expires_at > timezone('utc'::text, now())
            RETURNING id, organization_id, site_id, job_id
            """
        ),
        {"token_hash": token_hash},
    ).fetchone()

    if confirmed:
        db.execute(
            text(
                """
                UPDATE public.jobs
                SET decision_status = 'rejected',
                    rejected_at = timezone('utc'::text, now()),
                    rejection_source = 'user_veto'
                WHERE id = :job_id
                """
            ),
            {"job_id": str(confirmed.job_id)},
        )
        db.commit()
        return VetoConfirmResult(
            state="confirmed",
            message="Rollback request accepted.",
            veto_event_id=str(confirmed.id),
            site_id=str(confirmed.site_id),
            job_id=str(confirmed.job_id),
            organization_id=str(confirmed.organization_id),
        )

    existing = db.execute(
        text(
            """
            SELECT id, organization_id, site_id, job_id, status, expires_at, failure_reason
            FROM public.veto_events
            WHERE token_hash = :token_hash
            """
        ),
        {"token_hash": token_hash},
    ).fetchone()
    if not existing:
        return VetoConfirmResult(
            state="invalid_or_expired",
            message="This link is invalid or has expired.",
        )

    now_utc = datetime.now(timezone.utc)
    expires_at = existing.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if existing.status == "pending" and expires_at <= now_utc:
        return VetoConfirmResult(
            state="invalid_or_expired",
            message="This link is invalid or has expired.",
        )

    if existing.status == "failed":
        return VetoConfirmResult(
            state="rollback_failed",
            message="Rollback failed. Please retry from dashboard support flow.",
            veto_event_id=str(existing.id),
            site_id=str(existing.site_id),
            job_id=str(existing.job_id),
            organization_id=str(existing.organization_id),
            failure_reason=existing.failure_reason,
        )

    return VetoConfirmResult(
        state="already_processed",
        message="This change was already processed.",
        veto_event_id=str(existing.id),
        site_id=str(existing.site_id),
        job_id=str(existing.job_id),
        organization_id=str(existing.organization_id),
    )


def register_callback_secret_hash(
    db: Session,
    *,
    site_id: str,
    organization_id: str,
    secret_hash: str,
) -> None:
    """Persist callback secret hash for SaaS -> WP signed rollback calls."""
    db.execute(
        text(
            """
            UPDATE public.sites
            SET veto_callback_secret_hash = :secret_hash,
                veto_callback_secret_updated_at = timezone('utc'::text, now())
            WHERE id = :site_id
              AND organization_id = :organization_id
            """
        ),
        {
            "site_id": site_id,
            "organization_id": organization_id,
            "secret_hash": secret_hash,
        },
    )
    db.commit()


def mark_veto_event_result(
    db: Session,
    *,
    veto_event_id: str,
    status: str,
    failure_reason: Optional[str] = None,
) -> None:
    """Persist final rollback outcome."""
    params = {
        "veto_event_id": veto_event_id,
        "status": status,
        "failure_reason": failure_reason,
    }
    db.execute(
        text(
            """
            UPDATE public.veto_events
            SET status = :status,
                failure_reason = :failure_reason,
                rollback_completed_at = CASE
                    WHEN :status = 'rolled_back' THEN timezone('utc'::text, now())
                    ELSE rollback_completed_at
                END
            WHERE id = :veto_event_id
            """
        ),
        params,
    )
    db.commit()


def mark_job_rejection_failure_reason(
    db: Session,
    *,
    job_id: str,
    failure_reason: str,
) -> None:
    """Persist actionable failure reason while keeping job rejected."""
    db.execute(
        text(
            """
            UPDATE public.jobs
            SET decision_status = 'rejected',
                rejected_at = COALESCE(rejected_at, timezone('utc'::text, now())),
                rejection_source = :rejection_source
            WHERE id = :job_id
            """
        ),
        {
            "job_id": job_id,
            "rejection_source": f"user_veto_failed:{failure_reason}",
        },
    )
    db.commit()


def normalize_uuid(value: str | UUID) -> str:
    """Convert UUID-like objects to string."""
    return str(value)
