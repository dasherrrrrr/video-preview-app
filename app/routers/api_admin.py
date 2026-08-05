"""Management-API für Admin-Tokens (require_api_admin) - darüber kann Concorde
selbst Kunden anlegen und ihnen Videos zuweisen, statt dass ein Admin dafür
extra in die Video-Preview-App wechseln muss. Bewusst getrennt von /api/*
(Kunden-Tokens), damit ein Kunden-Token niemals Verwaltungsrechte hat."""

import json
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from ..api_auth import require_api_admin
from ..catalog import scan_library
from ..customers import (
    create_customer,
    generate_token_for_user,
    revoke_token_for_user,
    set_permissions,
    set_upload_folder,
    set_upload_quota,
)
from ..database import get_db
from ..uploads import get_quota_usage

router = APIRouter(prefix="/api/admin")


class CustomerCreate(BaseModel):
    username: str
    email: str = ""
    phone: str = ""


class VideoAssignment(BaseModel):
    video_ids: list[int]


class UploadFolderUpdate(BaseModel):
    upload_folder: str | None = None


class UploadQuotaUpdate(BaseModel):
    # Kontingent in Bytes, individuell für diesen Kunden. null = zurück auf
    # das globale Standard-Kontingent (uploads.DEFAULT_QUOTA_BYTES).
    quota_bytes: int | None = None


def _fetch_customer_or_404(user_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, email, phone, upload_folder, upload_quota_bytes FROM users WHERE id = ? AND is_admin = 0",
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


@router.put("/customers/{user_id}/upload-folder")
def update_customer_upload_folder(user_id: int, payload: UploadFolderUpdate, admin=Depends(require_api_admin)):
    """Legt fest, in welchem Ordner (relativ zum Videoverzeichnis, z.B. die
    schon für die Videozuweisung genutzte Ordner-ID) dieser Kunde per
    /api/upload Dateien hochladen darf. upload_folder=null deaktiviert den
    Upload für diesen Kunden wieder."""
    _fetch_customer_or_404(user_id)
    set_upload_folder(user_id, payload.upload_folder)
    return {"upload_folder": payload.upload_folder}


@router.put("/customers/{user_id}/upload-quota")
def update_customer_upload_quota(user_id: int, payload: UploadQuotaUpdate, admin=Depends(require_api_admin)):
    """Überschreibt das Upload-Kontingent für diesen einen Kunden, z.B. falls
    mehr Speicher benötigt wird. quota_bytes=null setzt wieder auf das
    globale Standard-Kontingent zurück."""
    _fetch_customer_or_404(user_id)
    if payload.quota_bytes is not None and payload.quota_bytes <= 0:
        raise HTTPException(status_code=400, detail="quota_bytes muss größer als 0 sein (oder null für den Standardwert).")
    set_upload_quota(user_id, payload.quota_bytes)
    return {"quota_bytes": payload.quota_bytes}


@router.get("/customers/{user_id}/upload")
def get_customer_upload_usage(user_id: int, admin=Depends(require_api_admin)):
    """Kontingent-Übersicht (Dateien + genutzter/verbleibender Speicher) für
    den Kunden - z.B. damit Concorde das später irgendwo anzeigen kann."""
    customer = _fetch_customer_or_404(user_id)
    if not customer["upload_folder"]:
        raise HTTPException(status_code=404, detail="Für diesen Kunden ist kein Upload freigeschaltet.")
    return get_quota_usage(customer["upload_folder"], customer["upload_quota_bytes"])


@router.post("/scan")
def scan_catalog(admin=Depends(require_api_admin)):
    """Liest das Videoverzeichnis neu ein (neue Dateien aufnehmen, gelöschte
    entfernen) und transkodiert neue Videos direkt vor. Kann bei vielen neuen
    Videos länger dauern (jedes wird einmal komplett transkodiert) - der
    Request läuft so lange synchron, ein Reverse-Proxy mit kurzem Timeout
    könnte die Verbindung vorher kappen. Der Scan läuft serverseitig aber
    trotzdem zu Ende, auch wenn der Concorde-Request währenddessen abbricht."""
    return scan_library()


@router.post("/incidents")
def report_incident(
    payload: dict,
    source: str | None = Query(default=None),
    x_incident_source: str | None = Header(default=None),
    admin=Depends(require_api_admin),
):
    """Nimmt Alarme von externen Monitoring-Quellen (Unraid Apprise-Agent,
    TrueNAS Alert-Service) entgegen und reicht sie als IT-Vorfall an Concorde
    weiter. Bewusst hier angesiedelt statt direkt in Concorde, weil Concorde
    unveröffentlicht ist und daher von außen nicht erreichbar - diese App ist
    es bereits (video.dominik-sturz.de) und hat schon eine Token-Auth.

    Akzeptiert sowohl Apprise-JSON ({"title", "message", "type"}) als auch
    Slack-kompatible Payloads ({"text": "..."}), wie sie z.B. TrueNAs'
    eingebauter "Slack"-Alert-Service verschickt.

    "source" kommt wahlweise als Query-Parameter oder als X-Incident-Source-
    Header - Apprises JSON-Webhook (apprise-go) reicht bei diesem Server
    keine Query-Parameter durch, nur Header, andere Absender (curl, TrueNAS)
    können weiterhin einfach den Query-Parameter nutzen."""
    source = source or x_incident_source
    if not source:
        raise HTTPException(status_code=400, detail="source fehlt (Query-Parameter oder X-Incident-Source-Header).")
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_ANON_KEY", "")
    incident_token = os.environ.get("INCIDENT_INGEST_TOKEN", "")
    if not supabase_url or not supabase_key or not incident_token:
        raise HTTPException(status_code=500, detail="Incident-Weiterleitung ist serverseitig nicht konfiguriert.")

    title = (payload.get("title") or "").strip()
    message = (payload.get("message") or payload.get("text") or "").strip()
    itype = (payload.get("type") or "warning").strip().lower()

    body = json.dumps(
        {
            "p_token": incident_token,
            "p_source": source,
            "p_title": title,
            "p_message": message,
            "p_type": itype,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/rpc/report_external_incident",
        data=body,
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": True, "incident_id": json.loads(resp.read())}
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Concorde-Weiterleitung fehlgeschlagen: {exc.read().decode()}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Concorde nicht erreichbar: {exc.reason}") from exc
