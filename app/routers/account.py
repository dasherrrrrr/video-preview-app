from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates

from ..auth import hash_password, require_login, verify_password
from ..database import get_db

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter()


@router.get("/account")
def account(request: Request, user=Depends(require_login)):
    return templates.TemplateResponse(
        "account.html", {"request": request, "user": user, "error": None, "success": None}
    )


@router.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    user=Depends(require_login),
):
    error = None
    if not verify_password(current_password, user["password_hash"]):
        error = "Aktuelles Passwort ist falsch."
    elif new_password != new_password_confirm:
        error = "Die neuen Passwörter stimmen nicht überein."
    elif len(new_password) < 8:
        error = "Neues Passwort muss mindestens 8 Zeichen lang sein."

    if error:
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "error": error, "success": None},
            status_code=400,
        )

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), user["id"]),
        )
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "user": user, "error": None, "success": "Passwort geändert."},
    )
