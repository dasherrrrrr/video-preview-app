"""
Mailversand über einen frei konfigurierbaren SMTP-Server (Gmail, web.de,
eigener Mailserver, ...). Werte kommen aus den Admin-Settings
(/admin/settings, gespeichert in der settings-Tabelle) mit Fallback auf
Umgebungsvariablen aus .env, falls in der DB noch nichts hinterlegt ist.
Ohne konfiguriertes SMTP_HOST wird der Versand übersprungen (kein Fehler) -
so bleibt die App auch ohne Mail-Konfiguration lauffähig.
"""

import smtplib
from email.mime.text import MIMEText

from .settings import get_setting


def _config() -> dict:
    return {
        "host": get_setting("smtp_host", "SMTP_HOST"),
        "port": int(get_setting("smtp_port", "SMTP_PORT", "587") or 587),
        "username": get_setting("smtp_username", "SMTP_USERNAME"),
        "password": get_setting("smtp_password", "SMTP_PASSWORD"),
        "from_addr": get_setting("smtp_from", "SMTP_FROM") or get_setting("smtp_username", "SMTP_USERNAME"),
        "use_ssl": get_setting("smtp_use_ssl", "SMTP_USE_SSL", "false").lower() == "true",
    }


def is_configured() -> bool:
    cfg = _config()
    return bool(cfg["host"] and cfg["from_addr"])


def send_email(to_address: str, subject: str, body: str) -> tuple[bool, str]:
    """Gibt (erfolg, fehlermeldung) zurück - die Fehlermeldung ist leer bei Erfolg,
    sonst menschenlesbar (z.B. für die "Test-Mail senden"-Funktion im Admin-UI)."""
    cfg = _config()
    if not is_configured():
        return False, "SMTP ist nicht konfiguriert."
    if not to_address:
        return False, "Keine Empfängeradresse angegeben."

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_address

    try:
        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
        with server:
            if not cfg["use_ssl"]:
                server.starttls()
            if cfg["username"] and cfg["password"]:
                server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["from_addr"], [to_address], msg.as_string())
        return True, ""
    except (smtplib.SMTPException, OSError) as exc:
        print(f"Mailversand an {to_address} fehlgeschlagen: {exc}")
        return False, str(exc)


def video_watch_url(video_id: int) -> str:
    base = get_setting("app_base_url", "APP_BASE_URL").rstrip("/")
    if base:
        return f"{base}/watch/{video_id}"
    return f"/watch/{video_id}"
