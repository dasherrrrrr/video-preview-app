"""Token-basierte Auth für externe Systeme (z.B. Lovable/Concorde), die Videos
per API abrufen wollen, aber keine Session-Cookie-Login-Seite dieser App
durchlaufen können. Ein Bearer-Token pro Nutzer, analog zum Passwort nur als
Hash gespeichert - der Klartext wird nur einmal beim Erzeugen angezeigt."""

import hashlib
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, status

from .auth import get_user_by_id
from .database import get_db


def generate_api_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_api_token(
    authorization: Optional[str] = Header(default=None),
    t: Optional[str] = Query(default=None),
):
    """FastAPI-Dependency: erwartet 'Authorization: Bearer <token>', mit
    Fallback auf einen ?t=-Query-Parameter (Name passend zu Concordes
    Implementierung). Der Fallback ist nötig, weil <video src>/<img src>-Tags
    (Concorde bettet /api/stream und /api/thumbnail direkt so ein) keine
    eigenen Header setzen können - für die JSON-Endpunkte sollte weiterhin
    der Header verwendet werden (landet sonst in Logs/URLs)."""
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.removeprefix("Bearer ").strip()
    elif t:
        raw_token = t.strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Fehlender oder ungültiger Authorization-Header bzw. t-Parameter.",
        )
    token_hash = hash_token(raw_token)

    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM api_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger API-Token.")
        conn.execute(
            "UPDATE api_tokens SET last_used_at = datetime('now') WHERE token_hash = ?",
            (token_hash,),
        )

    user = get_user_by_id(row["user_id"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nutzer zu diesem Token existiert nicht mehr.")
    return user


def require_api_admin(user=Depends(require_api_token)):
    """Wie require_api_token, aber nur für Admin-Tokens - für die
    Management-Endpunkte (/api/admin/*), über die z.B. Concorde Kunden anlegen
    und Videos zuweisen kann."""
    if not user["is_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur für Admin-Tokens.")
    return user
