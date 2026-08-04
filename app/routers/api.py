"""Token-basierte JSON-/Streaming-API für externe Clients (z.B. das Lovable-
Projekt "Concorde Manager"), die Videos ohne Session-Login dieser App
abspielen bzw. anzeigen wollen. Auth über Authorization: Bearer <token>
statt Cookie (siehe api_auth.require_api_token)."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..api_auth import require_api_token
from ..catalog import VIDEOS_DIR
from ..database import get_db
from ..mailer import send_email, video_watch_url
from ..media import build_download_response, build_stream_response, get_authorized_video
from ..thumbnails import get_thumbnail_path
from ..transcode import ensure_transcoded, needs_transcode

router = APIRouter(prefix="/api")

# Cross-Origin-Zugriff (Concorde/Lovable ruft diese API per fetch() aus dem
# Browser auf) wird zentral über CORSMiddleware in main.py erlaubt - inkl.
# Preflight-Handling für die POST/DELETE-Endpunkte hier unten.


class MarkerCreate(BaseModel):
    timestamp_seconds: float
    label: str


class CommentCreate(BaseModel):
    body: str


def _notify_assigned_editor(video_id: int, video_title: str, author_username: str, body: str) -> None:
    """Wenn ein Kunde (kein Admin) über Concorde kommentiert und ihm ein
    Bearbeiter zugewiesen ist, bekommt der Bearbeiter eine Mail - vorausgesetzt
    SMTP ist konfiguriert und der Bearbeiter hat eine E-Mail-Adresse hinterlegt."""
    with get_db() as conn:
        editor = conn.execute(
            "SELECT e.email FROM users u "
            "JOIN users e ON e.id = u.assigned_editor_id "
            "WHERE u.username = ?",
            (author_username,),
        ).fetchone()
    if not editor or not editor["email"]:
        return
    subject = f"Neuer Kommentar von {author_username}: {video_title}"
    message = (
        f"{author_username} hat einen neuen Kommentar geschrieben.\n\n"
        f"Video: {video_title}\n\n"
        f"{body}\n\n"
        f"Ansehen: {video_watch_url(video_id)}"
    )
    send_email(editor["email"], subject, message)


def _video_to_dict(video) -> dict:
    return {
        "id": video["id"],
        "title": video["title"],
        "duration_seconds": video["duration_seconds"],
        "thumbnail_url": f"/api/thumbnail/{video['id']}",
        "stream_url": f"/api/stream/{video['id']}",
        "download_url": f"/api/download/{video['id']}",
    }


@router.get("/videos")
def list_videos(user=Depends(require_api_token)):
    with get_db() as conn:
        if user["is_admin"]:
            videos = conn.execute(
                "SELECT id, title, duration_seconds FROM videos ORDER BY filepath"
            ).fetchall()
        else:
            videos = conn.execute(
                "SELECT v.id, v.title, v.duration_seconds "
                "FROM videos v JOIN permissions p ON p.video_id = v.id "
                "WHERE p.user_id = ? ORDER BY v.filepath",
                (user["id"],),
            ).fetchall()
    return [_video_to_dict(v) for v in videos]


@router.get("/videos/{video_id}")
def video_detail(video_id: int, user=Depends(require_api_token)):
    video = get_authorized_video(video_id, user)
    with get_db() as conn:
        comments = conn.execute(
            "SELECT c.id, c.body, c.created_at, u.username "
            "FROM comments c JOIN users u ON u.id = c.user_id "
            "WHERE c.video_id = ? ORDER BY c.created_at",
            (video_id,),
        ).fetchall()
        markers = conn.execute(
            "SELECT id, timestamp_seconds, label FROM markers "
            "WHERE user_id = ? AND video_id = ? ORDER BY timestamp_seconds",
            (user["id"], video_id),
        ).fetchall()
    return {
        **_video_to_dict(video),
        "comments": [dict(c) for c in comments],
        "markers": [dict(m) for m in markers],
    }


@router.get("/thumbnail/{video_id}")
def api_thumbnail(video_id: int, user=Depends(require_api_token)):
    get_authorized_video(video_id, user)
    path = get_thumbnail_path(video_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Kein Vorschaubild vorhanden.")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/stream/{video_id}")
def api_stream_video(video_id: int, request: Request, user=Depends(require_api_token)):
    video = get_authorized_video(video_id, user)
    filepath = VIDEOS_DIR / video["filepath"]
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht (mehr) vorhanden.")

    if needs_transcode(video["codec"]):
        try:
            filepath = ensure_transcoded(video_id, filepath)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return build_stream_response(filepath, request.headers.get("range"))


@router.get("/download/{video_id}")
def api_download_video(video_id: int, user=Depends(require_api_token)):
    """Download des Original-Videos in voller Qualität (nicht die 720p-
    Vorschauversion) - Bandbreite optional über /admin/settings gedrosselt."""
    video = get_authorized_video(video_id, user)
    filepath = VIDEOS_DIR / video["filepath"]
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht (mehr) vorhanden.")
    return build_download_response(filepath, Path(video["filepath"]).name)


@router.post("/videos/{video_id}/markers")
def create_marker(video_id: int, payload: MarkerCreate, user=Depends(require_api_token)):
    get_authorized_video(video_id, user)  # wirft 403/404, falls kein Zugriff
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Marker brauchen eine Beschreibung.")
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO markers (user_id, video_id, timestamp_seconds, label) VALUES (?, ?, ?, ?)",
            (user["id"], video_id, payload.timestamp_seconds, label),
        )
    return {"id": cursor.lastrowid, "timestamp_seconds": payload.timestamp_seconds, "label": label}


@router.delete("/markers/{marker_id}")
def delete_marker(marker_id: int, user=Depends(require_api_token)):
    with get_db() as conn:
        marker = conn.execute(
            "SELECT id FROM markers WHERE id = ? AND user_id = ?", (marker_id, user["id"])
        ).fetchone()
        if not marker:
            raise HTTPException(status_code=404, detail="Marker nicht gefunden.")
        conn.execute("DELETE FROM markers WHERE id = ?", (marker_id,))
    return {"ok": True}


@router.post("/videos/{video_id}/comments")
def create_comment(video_id: int, payload: CommentCreate, user=Depends(require_api_token)):
    video = get_authorized_video(video_id, user)  # wirft 403/404, falls kein Zugriff
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Kommentar darf nicht leer sein.")
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO comments (user_id, video_id, body) VALUES (?, ?, ?)",
            (user["id"], video_id, body),
        )
    if not user["is_admin"]:
        _notify_assigned_editor(video_id, video["title"], user["username"], body)
    return {"id": cursor.lastrowid, "body": body, "user_id": user["id"]}


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, user=Depends(require_api_token)):
    with get_db() as conn:
        comment = conn.execute("SELECT id, user_id FROM comments WHERE id = ?", (comment_id,)).fetchone()
        if not comment:
            raise HTTPException(status_code=404, detail="Kommentar nicht gefunden.")
        if comment["user_id"] != user["id"] and not user["is_admin"]:
            raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Kommentar.")
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    return {"ok": True}
