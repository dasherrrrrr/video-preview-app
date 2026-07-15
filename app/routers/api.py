"""Token-basierte JSON-/Streaming-API für externe Clients (z.B. das Lovable-
Projekt "Concorde Manager"), die Videos ohne Session-Login dieser App
abspielen bzw. anzeigen wollen. Auth über Authorization: Bearer <token>
statt Cookie (siehe api_auth.require_api_token)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..api_auth import require_api_token
from ..catalog import VIDEOS_DIR
from ..database import get_db
from ..media import build_stream_response, get_authorized_video
from ..thumbnails import get_thumbnail_path
from ..transcode import ensure_transcoded, needs_transcode

router = APIRouter(prefix="/api")

# Erlaubt Cross-Origin-Zugriff aus dem Browser (fetch aus Concorde/Lovable).
# Unbedenklich mit Wildcard, weil die Auth über einen explizit vom Nutzer
# gesendeten Bearer-Token läuft, nicht über Cookies - ein fremdes Origin kann
# sich den Token nicht "erschleichen", nur wer ihn kennt kommt rein.
CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}


def _video_to_dict(video) -> dict:
    return {
        "id": video["id"],
        "title": video["title"],
        "duration_seconds": video["duration_seconds"],
        "thumbnail_url": f"/api/thumbnail/{video['id']}",
        "stream_url": f"/api/stream/{video['id']}",
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
    return FileResponse(path, media_type="image/jpeg", headers=CORS_HEADERS)


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

    return build_stream_response(filepath, request.headers.get("range"), extra_headers=CORS_HEADERS)
