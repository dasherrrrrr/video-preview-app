from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse

from ..auth import require_login
from ..database import get_db
from ..media import get_authorized_video

router = APIRouter()


@router.post("/watch/{video_id}/markers")
def create_marker(
    video_id: int,
    timestamp_seconds: float = Form(...),
    label: str = Form(...),
    user=Depends(require_login),
):
    get_authorized_video(video_id, user)  # wirft 403/404, falls kein Zugriff
    label = label.strip()
    if not label:
        raise HTTPException(
            status_code=400,
            detail="Marker brauchen eine Beschreibung (was soll an dieser Stelle anders sein oder gefällt).",
        )
    with get_db() as conn:
        conn.execute(
            "INSERT INTO markers (user_id, video_id, timestamp_seconds, label) "
            "VALUES (?, ?, ?, ?)",
            (user["id"], video_id, timestamp_seconds, label),
        )
    return RedirectResponse(url=f"/watch/{video_id}", status_code=303)


@router.post("/watch/{video_id}/markers/{marker_id}/delete")
def delete_marker(video_id: int, marker_id: int, user=Depends(require_login)):
    with get_db() as conn:
        marker = conn.execute(
            "SELECT id FROM markers WHERE id = ? AND video_id = ? AND user_id = ?",
            (marker_id, video_id, user["id"]),
        ).fetchone()
        if not marker:
            raise HTTPException(status_code=404, detail="Marker nicht gefunden.")
        conn.execute("DELETE FROM markers WHERE id = ?", (marker_id,))
    return RedirectResponse(url=f"/watch/{video_id}", status_code=303)
