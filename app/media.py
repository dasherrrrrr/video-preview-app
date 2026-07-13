"""Gemeinsame Berechtigungsprüfung für Player-Seite und Streaming-Endpoint."""

from fastapi import HTTPException

from .database import get_db


def get_authorized_video(video_id: int, user):
    with get_db() as conn:
        video = conn.execute(
            "SELECT id, filepath, title, duration_seconds, codec FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if not video:
            raise HTTPException(status_code=404, detail="Video nicht gefunden.")
        if not user["is_admin"]:
            allowed = conn.execute(
                "SELECT 1 FROM permissions WHERE user_id = ? AND video_id = ?",
                (user["id"], video_id),
            ).fetchone()
            if not allowed:
                raise HTTPException(status_code=403, detail="Kein Zugriff auf dieses Video.")
    return video
