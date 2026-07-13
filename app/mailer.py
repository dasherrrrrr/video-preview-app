"""
Mailversand über einen frei konfigurierbaren SMTP-Server (Gmail, web.de,
eigener Mailserver, ...) - alle Zugangsdaten kommen aus Umgebungsvariablen
(siehe .env.example), nichts ist fest verdrahtet. Ohne gesetztes SMTP_HOST
wird der Versand übersprungen (kein Fehler) - so bleibt die App auch ohne
Mail-Konfiguration lauffähig.
"""

import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM") or SMTP_USERNAME
# "true" für Server, die direkt SSL verlangen (meist Port 465, z.B. manche
# web.de-Konfigurationen) - Standardfall ist STARTTLS auf Port 587 (Gmail u.a.).
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "false").lower() == "true"

APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")


def is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def send_email(to_address: str, subject: str, body: str) -> bool:
    if not is_configured() or not to_address:
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_address

    try:
        if SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
        with server:
            if not SMTP_USE_SSL:
                server.starttls()
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_address], msg.as_string())
        return True
    except (smtplib.SMTPException, OSError) as exc:
        print(f"Mailversand an {to_address} fehlgeschlagen: {exc}")
        return False


def video_watch_url(video_id: int) -> str:
    if APP_BASE_URL:
        return f"{APP_BASE_URL}/watch/{video_id}"
    return f"/watch/{video_id}"
