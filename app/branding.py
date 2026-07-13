"""
Logo und Favicon, die Admins unter /admin/settings hochladen können. Dateien
landen im data-Volume (überleben also Container-Updates), der jeweilige
Content-Type wird in der settings-Tabelle gemerkt, damit /branding/<kind>
ihn beim Ausliefern korrekt setzen kann - unabhängig vom Dateiformat, das
hochgeladen wurde (PNG, SVG, ICO, ...).
"""

from pathlib import Path

from .database import DB_PATH
from .settings import get_setting, set_setting

BRANDING_DIR = DB_PATH.parent / "branding"

ALLOWED_TYPES = {
    "logo": {"image/png", "image/jpeg", "image/svg+xml"},
    "favicon": {"image/png", "image/x-icon", "image/vnd.microsoft.icon", "image/svg+xml"},
}

MAX_SIZE = 2 * 1024 * 1024  # 2 MB - Logos/Favicons müssen nicht größer sein


def get_branding_path(kind: str) -> Path:
    return BRANDING_DIR / kind


def get_branding_content_type(kind: str) -> str:
    return get_setting(f"{kind}_content_type")


def has_branding(kind: str) -> bool:
    return get_branding_path(kind).is_file()


def set_branding_file(kind: str, content: bytes, content_type: str) -> None:
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    get_branding_path(kind).write_bytes(content)
    set_setting(f"{kind}_content_type", content_type)


def clear_branding_file(kind: str) -> None:
    get_branding_path(kind).unlink(missing_ok=True)
    set_setting(f"{kind}_content_type", "")
