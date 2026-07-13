from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..auth import get_current_user, get_user_by_username, verify_password
from ..templates_env import templates

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": None}
    )


@router.post("/login")
def login_submit(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Benutzername oder Passwort falsch."},
            status_code=401,
        )
    request.session["user_id"] = user["id"]
    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
