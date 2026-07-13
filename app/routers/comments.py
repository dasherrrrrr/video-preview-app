from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse

from ..auth import require_login
from ..database import get_db
from ..mailer import send_email, video_watch_url
from ..media import get_authorized_video

router = APIRouter()


def _notify_assigned_editor(video_id: int, video_title: str, author_username: str, body: str) -> None:
    """Wenn ein Kunde (kein Admin) kommentiert und ihm ein Bearbeiter zugewiesen
    ist, bekommt der Bearbeiter eine Mail - vorausgesetzt SMTP ist konfiguriert
    und der Bearbeiter hat eine E-Mail-Adresse hinterlegt."""
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


@router.post("/watch/{video_id}/comments")
def create_comment(video_id: int, body: str = Form(...), user=Depends(require_login)):
    video = get_authorized_video(video_id, user)  # wirft 403/404, falls kein Zugriff
    body = body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Kommentar darf nicht leer sein.")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO comments (user_id, video_id, body) VALUES (?, ?, ?)",
            (user["id"], video_id, body),
        )
    if not user["is_admin"]:
        _notify_assigned_editor(video_id, video["title"], user["username"], body)
    return RedirectResponse(url=f"/watch/{video_id}", status_code=303)


@router.post("/watch/{video_id}/comments/{comment_id}/delete")
def delete_comment(video_id: int, comment_id: int, user=Depends(require_login)):
    get_authorized_video(video_id, user)
    with get_db() as conn:
        comment = conn.execute(
            "SELECT id, user_id FROM comments WHERE id = ? AND video_id = ?",
            (comment_id, video_id),
        ).fetchone()
        if not comment:
            raise HTTPException(status_code=404, detail="Kommentar nicht gefunden.")
        # Eigene Kommentare darf jeder löschen, fremde nur ein Admin.
        if comment["user_id"] != user["id"] and not user["is_admin"]:
            raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Kommentar.")
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    return RedirectResponse(url=f"/watch/{video_id}", status_code=303)
