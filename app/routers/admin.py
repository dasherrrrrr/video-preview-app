import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from ..api_auth import generate_api_token, hash_token
from ..auth import hash_password, require_admin
from ..catalog import scan_library
from ..database import get_db
from ..media import get_authorized_video
from ..templates_env import templates
from ..thumbnails import set_custom_thumbnail

ALLOWED_THUMBNAIL_TYPES = {"image/png", "image/jpeg"}
MAX_THUMBNAIL_SIZE = 15 * 1024 * 1024  # 15 MB

router = APIRouter(prefix="/admin")


def _fetch_users():
    with get_db() as conn:
        return conn.execute(
            "SELECT u.id, u.username, u.is_admin, u.email, u.phone, u.created_at, "
            "u.assigned_editor_id, e.username AS editor_username, "
            "t.created_at AS api_token_created_at "
            "FROM users u LEFT JOIN users e ON e.id = u.assigned_editor_id "
            "LEFT JOIN api_tokens t ON t.user_id = u.id "
            "ORDER BY u.id"
        ).fetchall()


def _fetch_editors():
    """Admins, die als Bearbeiter einem Kunden zugewiesen werden können."""
    with get_db() as conn:
        return conn.execute(
            "SELECT id, username FROM users WHERE is_admin = 1 ORDER BY username"
        ).fetchall()


@router.get("/users")
def list_users(request: Request, admin=Depends(require_admin), new_token: Optional[str] = None):
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "user": admin,
            "users": _fetch_users(),
            "editors": _fetch_editors(),
            "error": None,
            "new_token": new_token,
        },
    )


@router.post("/users")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    is_admin: str = Form(""),  # Checkbox: kommt als "on" wenn angehakt, sonst gar nicht
    admin=Depends(require_admin),
):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            return templates.TemplateResponse(
                "admin_users.html",
                {
                    "request": request,
                    "user": admin,
                    "users": _fetch_users(),
                    "editors": _fetch_editors(),
                    "error": f"Benutzername '{username}' existiert bereits.",
                },
                status_code=400,
            )
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username, hash_password(password), 1 if is_admin == "on" else 0),
        )
    return RedirectResponse(url="/admin/users", status_code=303)


def _fetch_videos():
    with get_db() as conn:
        return conn.execute(
            "SELECT id, filepath, title, duration_seconds, codec, added_at "
            "FROM videos ORDER BY filepath"
        ).fetchall()


@router.get("/videos")
def list_videos(request: Request, admin=Depends(require_admin), scan_result: Optional[str] = None):
    return templates.TemplateResponse(
        "admin_videos.html",
        {
            "request": request,
            "user": admin,
            "videos": _fetch_videos(),
            "scan_result": scan_result,
        },
    )


@router.post("/videos/scan")
def scan_videos(admin=Depends(require_admin)):
    result = scan_library()
    summary = f"{result['added']} neu, {result['removed']} entfernt, {result['unchanged']} unverändert"
    return RedirectResponse(url=f"/admin/videos?scan_result={summary}", status_code=303)


@router.post("/videos/{video_id}/rename")
def rename_video(video_id: int, title: str = Form(...), admin=Depends(require_admin)):
    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Titel darf nicht leer sein.")
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM videos WHERE id = ?", (video_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Video nicht gefunden.")
        conn.execute("UPDATE videos SET title = ? WHERE id = ?", (title, video_id))
    return RedirectResponse(url="/admin/videos", status_code=303)


@router.post("/videos/{video_id}/thumbnail")
async def upload_thumbnail(
    video_id: int,
    thumbnail_file: UploadFile = File(...),
    admin=Depends(require_admin),
):
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Video nicht gefunden.")

    if thumbnail_file.content_type not in ALLOWED_THUMBNAIL_TYPES:
        raise HTTPException(status_code=400, detail="Nur PNG oder JPG erlaubt.")

    contents = await thumbnail_file.read(MAX_THUMBNAIL_SIZE + 1)
    if len(contents) > MAX_THUMBNAIL_SIZE:
        raise HTTPException(status_code=400, detail="Datei zu groß (max. 15 MB).")

    suffix = Path(thumbnail_file.filename or "").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        set_custom_thumbnail(video_id, tmp_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return RedirectResponse(url="/admin/videos", status_code=303)


def _fetch_user_or_404(user_id: int):
    with get_db() as conn:
        target = conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    return target


@router.post("/users/{user_id}/editor")
def assign_editor(user_id: int, editor_id: str = Form(""), admin=Depends(require_admin)):
    _fetch_user_or_404(user_id)
    editor_id_int = None
    if editor_id:
        editor_id_int = int(editor_id)
        with get_db() as conn:
            editor = conn.execute(
                "SELECT id FROM users WHERE id = ? AND is_admin = 1", (editor_id_int,)
            ).fetchone()
        if not editor:
            raise HTTPException(status_code=400, detail="Ungültiger Bearbeiter.")
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET assigned_editor_id = ? WHERE id = ?",
            (editor_id_int, user_id),
        )
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/api-token")
def create_api_token(user_id: int, admin=Depends(require_admin)):
    _fetch_user_or_404(user_id)
    token = generate_api_token()
    with get_db() as conn:
        conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO api_tokens (user_id, token_hash) VALUES (?, ?)",
            (user_id, hash_token(token)),
        )
    return RedirectResponse(url=f"/admin/users?new_token={token}", status_code=303)


@router.post("/users/{user_id}/api-token/revoke")
def revoke_api_token(user_id: int, admin=Depends(require_admin)):
    _fetch_user_or_404(user_id)
    with get_db() as conn:
        conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
    return RedirectResponse(url="/admin/users", status_code=303)


def _video_folder(filepath: str) -> str:
    return filepath.rsplit("/", 1)[0] if "/" in filepath else "(Hauptverzeichnis)"


@router.get("/users/{user_id}/permissions")
def edit_permissions(user_id: int, request: Request, admin=Depends(require_admin)):
    target = _fetch_user_or_404(user_id)
    with get_db() as conn:
        videos = conn.execute(
            "SELECT id, filepath, title FROM videos ORDER BY filepath"
        ).fetchall()
        assigned_ids = {
            row["video_id"]
            for row in conn.execute(
                "SELECT video_id FROM permissions WHERE user_id = ?", (user_id,)
            ).fetchall()
        }

    # Nach Ordner gruppieren (z.B. Datumsordner der DJI-Clips), damit Admins
    # ganze Ordner auf einmal zuweisen können statt jedes Video einzeln.
    groups = []
    groups_by_folder = {}
    for v in videos:
        folder = _video_folder(v["filepath"])
        if folder not in groups_by_folder:
            groups_by_folder[folder] = {"folder": folder, "videos": []}
            groups.append(groups_by_folder[folder])
        groups_by_folder[folder]["videos"].append(v)

    return templates.TemplateResponse(
        "admin_permissions.html",
        {
            "request": request,
            "user": admin,
            "target": target,
            "groups": groups,
            "assigned_ids": assigned_ids,
        },
    )


@router.post("/users/{user_id}/permissions")
async def update_permissions(user_id: int, request: Request, admin=Depends(require_admin)):
    _fetch_user_or_404(user_id)
    form = await request.form()
    video_ids = [int(v) for v in form.getlist("video_ids")]
    with get_db() as conn:
        conn.execute("DELETE FROM permissions WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO permissions (user_id, video_id) VALUES (?, ?)",
            [(user_id, video_id) for video_id in video_ids],
        )
    return RedirectResponse(url=f"/admin/users/{user_id}/permissions", status_code=303)


@router.get("/videos/{video_id}")
def video_detail(video_id: int, request: Request, admin=Depends(require_admin)):
    video = get_authorized_video(video_id, admin)
    with get_db() as conn:
        markers = conn.execute(
            "SELECT m.timestamp_seconds, m.label, u.username "
            "FROM markers m JOIN users u ON u.id = m.user_id "
            "WHERE m.video_id = ? ORDER BY m.timestamp_seconds",
            (video_id,),
        ).fetchall()
        comments = conn.execute(
            "SELECT c.id, c.body, c.created_at, u.username "
            "FROM comments c JOIN users u ON u.id = c.user_id "
            "WHERE c.video_id = ? ORDER BY c.created_at",
            (video_id,),
        ).fetchall()
    return templates.TemplateResponse(
        "admin_video_detail.html",
        {
            "request": request,
            "user": admin,
            "video": video,
            "markers": markers,
            "comments": comments,
        },
    )


@router.post("/videos/{video_id}/comments")
def admin_reply(video_id: int, body: str = Form(...), admin=Depends(require_admin)):
    get_authorized_video(video_id, admin)
    body = body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Kommentar darf nicht leer sein.")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO comments (user_id, video_id, body) VALUES (?, ?, ?)",
            (admin["id"], video_id, body),
        )
    return RedirectResponse(url=f"/admin/videos/{video_id}", status_code=303)


@router.post("/videos/{video_id}/comments/{comment_id}/delete")
def admin_delete_comment(video_id: int, comment_id: int, admin=Depends(require_admin)):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM comments WHERE id = ? AND video_id = ?", (comment_id, video_id)
        )
    return RedirectResponse(url=f"/admin/videos/{video_id}", status_code=303)


@router.get("/markers")
def list_all_markers(request: Request, admin=Depends(require_admin)):
    with get_db() as conn:
        markers = conn.execute(
            "SELECT m.id, m.timestamp_seconds, m.label, m.created_at, "
            "u.username, v.id AS video_id, v.title AS video_title "
            "FROM markers m "
            "JOIN users u ON u.id = m.user_id "
            "JOIN videos v ON v.id = m.video_id "
            "ORDER BY v.filepath, u.username, m.timestamp_seconds"
        ).fetchall()
    return templates.TemplateResponse(
        "admin_markers.html", {"request": request, "user": admin, "markers": markers}
    )
