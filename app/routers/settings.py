from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_admin
from ..mailer import is_configured, send_email
from ..settings import get_setting, set_setting

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter(prefix="/admin")

SETTINGS_FIELDS = [
    ("smtp_host", "SMTP_HOST"),
    ("smtp_port", "SMTP_PORT"),
    ("smtp_username", "SMTP_USERNAME"),
    ("smtp_from", "SMTP_FROM"),
    ("smtp_use_ssl", "SMTP_USE_SSL"),
    ("app_base_url", "APP_BASE_URL"),
]


def _current_settings() -> dict:
    values = {key: get_setting(key, env_fallback) for key, env_fallback in SETTINGS_FIELDS}
    values["smtp_password_set"] = bool(get_setting("smtp_password", "SMTP_PASSWORD"))
    return values


@router.get("/settings")
def show_settings(
    request: Request,
    admin=Depends(require_admin),
    saved: Optional[str] = None,
    test_result: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin_settings.html",
        {
            "request": request,
            "user": admin,
            "settings": _current_settings(),
            "smtp_configured": is_configured(),
            "saved": saved == "1",
            "test_result": test_result,
        },
    )


@router.post("/settings")
def save_settings(
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_use_ssl: str = Form(""),  # Checkbox: "on" wenn angehakt, sonst gar nicht
    app_base_url: str = Form(""),
    admin=Depends(require_admin),
):
    set_setting("smtp_host", smtp_host.strip())
    set_setting("smtp_port", smtp_port.strip() or "587")
    set_setting("smtp_username", smtp_username.strip())
    set_setting("smtp_from", smtp_from.strip())
    set_setting("smtp_use_ssl", "true" if smtp_use_ssl == "on" else "false")
    set_setting("app_base_url", app_base_url.strip())
    # Passwort nur überschreiben, wenn tatsächlich etwas eingegeben wurde -
    # leeres Feld beim Speichern soll das bestehende Passwort nicht löschen.
    if smtp_password:
        set_setting("smtp_password", smtp_password)
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


@router.post("/settings/test-mail")
def send_test_mail(test_email: str = Form(...), admin=Depends(require_admin)):
    success, error = send_email(
        test_email.strip(),
        "Testmail - Video Preview App",
        "Diese Mail bestätigt, dass die SMTP-Konfiguration der Video Preview App funktioniert.",
    )
    result = "ok" if success else f"error:{error}"
    return RedirectResponse(url=f"/admin/settings?test_result={quote(result)}", status_code=303)
