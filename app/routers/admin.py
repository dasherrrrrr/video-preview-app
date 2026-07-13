import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import hash_password, require_admin
from ..catalog import scan_library
from ..database import get_db
from ..thumbnails import set_custom_thumbnail

ALLOWED_THUMBNAIL_TYPES = {"image/png", "image/jpeg"}
MAX_THUMBNAIL_SIZE = 15 * 1024 * 1024  # 15 MB

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter(prefix="/admin")


def _fetch_users():
    with get_db() as conn:
        return conn.execute(
            "SELECT id, username, is_admin, created_at FROM users ORDER BY id"
        ).fetchall()


@router.get("/users")
def list_users(request: Request, admin=Depends(require_admin)):
    return templates.TemplateResponse(
        "admin_users.html",
        {"request": request, "user": admin, "users": _fetch_users(), "error": None},
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
