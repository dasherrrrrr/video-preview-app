"""
Einstellungen, die Admins über /admin/settings im Web-UI pflegen können
(z.B. SMTP-Zugangsdaten), statt sie auf dem Server in der .env-Datei zu
editieren. Fällt auf eine Umgebungsvariable zurück, falls in der DB noch
nichts hinterlegt ist - bestehende .env-Konfigurationen funktionieren also
weiter, bis jemand die Einstellung über die Oberfläche überschreibt.
"""

import os

from .database import get_db


def get_setting(key: str, env_fallback: str = "", default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None and row["value"] is not None:
        return row["value"]
    if env_fallback:
        return os.environ.get(env_fallback, default)
    return default


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
