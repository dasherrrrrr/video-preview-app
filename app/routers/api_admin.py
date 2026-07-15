"""Management-API für Admin-Tokens (require_api_admin) - darüber kann Concorde
selbst Kunden anlegen und ihnen Videos zuweisen, statt dass ein Admin dafür
extra in die Video-Preview-App wechseln muss. Bewusst getrennt von /api/*
(Kunden-Tokens), damit ein Kunden-Token niemals Verwaltungsrechte hat."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..api_auth import require_api_admin
from ..customers import create_customer, generate_token_for_user, revoke_token_for_user, set_permissions
from ..database import get_db

router = APIRouter(prefix="/api/admin")


class CustomerCreate(BaseModel):
    username: str
    email: str = ""
    phone: str = ""


class VideoAssignment(BaseModel):
    video_ids: list[int]


def _fetch_customer_or_404(user_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, email, phone FROM users WHERE id = ? AND is_admin = 0",
            (user_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden.")
    return row


@router.get("/customers")
def list_customers(admin=Depends(require_api_admin)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT u.id, u.username, u.email, u.phone, "
            "(t.user_id IS NOT NULL) AS has_token, "
            "(SELECT COUNT(*) FROM permissions p WHERE p.user_id = u.id) AS video_count "
            "FROM users u LEFT JOIN api_tokens t ON t.user_id = u.id "
            "WHERE u.is_admin = 0 ORDER BY u.username"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/customers")
def create_customer_endpoint(payload: CustomerCreate, admin=Depends(require_api_admin)):
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Benutzername darf nicht leer sein.")
    user_id = create_customer(username, payload.email.strip(), payload.phone.strip())
    return {"id": user_id, "username": username}


@router.post("/customers/{user_id}/token")
def create_customer_token(user_id: int, admin=Depends(require_api_admin)):
    _fetch_customer_or_404(user_id)
    token = generate_token_for_user(user_id)
    return {"token": token}


@router.delete("/customers/{user_id}/token")
def revoke_customer_token(user_id: int, admin=Depends(require_api_admin)):
    _fetch_customer_or_404(user_id)
    revoke_token_for_user(user_id)
    return {"ok": True}


@router.get("/catalog")
def list_catalog(admin=Depends(require_api_admin)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, filepath, duration_seconds FROM videos ORDER BY filepath"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/customers/{user_id}/videos")
def get_customer_videos(user_id: int, admin=Depends(require_api_admin)):
    _fetch_customer_or_404(user_id)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT video_id FROM permissions WHERE user_id = ?", (user_id,)
        ).fetchall()
    return {"video_ids": [r["video_id"] for r in rows]}


@router.put("/customers/{user_id}/videos")
def assign_videos(user_id: int, payload: VideoAssignment, admin=Depends(require_api_admin)):
    _fetch_customer_or_404(user_id)
    set_permissions(user_id, payload.video_ids)
    return {"video_ids": payload.video_ids}
