from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from ..auth import require_login
from ..database import get_db

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
