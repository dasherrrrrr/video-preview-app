from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
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
