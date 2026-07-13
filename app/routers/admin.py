from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import hash_password, require_admin
from ..catalog import scan_library
from ..database import get_db

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


def _fetch_user_or_404(user_id: int):
    with get_db() as conn:
        target = conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    return target


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
    return templates.TemplateResponse(
        "admin_permissions.html",
        {
            "request": request,
            "user": admin,
            "target": target,
            "videos": videos,
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
