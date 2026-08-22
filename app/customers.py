"""Gemeinsame Kunden-Verwaltungslogik - genutzt sowohl vom Admin-Session-UI
(app/routers/admin.py) als auch von der Token-Admin-API (app/routers/api_admin.py),
damit Concorde dieselben Aktionen (Kunde anlegen, Videos zuweisen, Token
erzeugen) ausführen kann wie ein Admin direkt in dieser App."""

import os
import secrets
import threading
from pathlib import Path

from fastapi import HTTPException

from .api_auth import generate_api_token, hash_token
from .auth import hash_password
from .database import get_db
from .transcode import ensure_transcoded, get_cache_path, needs_transcode

VIDEOS_DIR = Path(os.environ.get("VIDEOS_DIR", "/videos"))


def create_customer(username: str, email: str = "", phone: str = "") -> int:
    """Legt einen neuen Kunden (is_admin=0) an. Kunden loggen sich nicht mehr
    direkt in dieser App ein (nur noch über Concorde) - das Passwort ist daher
    nur ein technischer Platzhalter, den niemand kennt oder braucht."""
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail=f"Benutzername '{username}' existiert bereits.")
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, email, phone) "
            "VALUES (?, ?, 0, ?, ?)",
            (username, hash_password(secrets.token_urlsafe(24)), email or None, phone or None),
        )
        return cursor.lastrowid


def generate_token_for_user(user_id: int) -> str:
    token = generate_api_token()
    with get_db() as conn:
        conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO api_tokens (user_id, token_hash) VALUES (?, ?)",
            (user_id, hash_token(token)),
        )
    return token


def revoke_token_for_user(user_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))


def set_permissions(user_id: int, video_ids: list[int]) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM permissions WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO permissions (user_id, video_id) VALUES (?, ?)",
            [(user_id, video_id) for video_id in video_ids],
        )
    _transcode_in_background(video_ids)


def _transcode_in_background(video_ids: list[int]) -> None:
    """Videos werden nicht mehr beim Katalog-Scan pauschal transkodiert
    (bei zehntausenden Dateien viel zu teuer), sondern erst wenn sie einem
    Kunden zugewiesen werden - genau dann werden sie tatsächlich gebraucht.
    Läuft im Hintergrund-Thread, damit die Zuweisung selbst nicht auf
    (ggf. viele) Transcodes warten muss; /api/stream transkodiert notfalls
    trotzdem noch on-demand, falls ein Kunde das Video öffnet, bevor dieser
    Hintergrund-Thread fertig ist."""

    def _run():
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT id, filepath, codec, bit_rate, width FROM videos WHERE id IN "
                f"({','.join('?' for _ in video_ids)})",
                video_ids,
            ).fetchall()
        for row in rows:
            if not needs_transcode(row["codec"], row["bit_rate"], row["width"]):
                continue
            if get_cache_path(row["id"]).is_file():
                continue
            try:
                ensure_transcoded(row["id"], VIDEOS_DIR / row["filepath"])
            except RuntimeError:
                pass

    if video_ids:
        threading.Thread(target=_run, daemon=True).start()


def set_upload_folder(user_id: int, folder: str | None) -> None:
    """Setzt den Ordner (relativ zu VIDEOS_DIR), in dem dieser Kunde per
    /api/upload Dateien hochladen darf - siehe app/uploads.py. None/leer
    deaktiviert den Upload für diesen Kunden wieder."""
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET upload_folder = ? WHERE id = ?",
            (folder.strip() if folder and folder.strip() else None, user_id),
        )


def set_upload_quota(user_id: int, quota_bytes: int | None) -> None:
    """Überschreibt das Upload-Kontingent für diesen einen Kunden (z.B. wenn
    mehr Speicher gebraucht wird). None setzt wieder auf das globale
    Standard-Kontingent zurück (uploads.DEFAULT_QUOTA_BYTES)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET upload_quota_bytes = ? WHERE id = ?",
            (quota_bytes if quota_bytes and quota_bytes > 0 else None, user_id),
        )
