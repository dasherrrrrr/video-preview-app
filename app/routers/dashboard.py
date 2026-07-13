from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from ..auth import require_login
from ..database import get_db
from ..media import get_authorized_video

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter()


@router.get("/")
def dashboard(request: Request, user=Depends(require_login)):
    with get_db() as conn:
        videos = conn.execute(
            "SELECT v.id, v.title, v.filepath, v.duration_seconds "
            "FROM videos v "
            "JOIN permissions p ON p.video_id = v.id "
            "WHERE p.user_id = ? "
            "ORDER BY v.filepath",
            (user["id"],),
        ).fetchall()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user, "videos": videos}
    )


@router.get("/watch/{video_id}")
def watch(video_id: int, request: Request, user=Depends(require_login)):
    video = get_authorized_video(video_id, user)
    with get_db() as conn:
        markers = conn.execute(
            "SELECT id, timestamp_seconds, label FROM markers "
            "WHERE user_id = ? AND video_id = ? ORDER BY timestamp_seconds",
            (user["id"], video_id),
        ).fetchall()
    return templates.TemplateResponse(
        "watch.html",
        {"request": request, "user": user, "video": video, "markers": markers},
    )
