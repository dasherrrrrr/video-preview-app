"""Datei-Upload für Kunden (Bilder/kleine Videos, Kontingent individuell pro
Kunde einstellbar - siehe app/customers.py:set_upload_quota) - die eigentliche
Logik steckt in app/uploads.py. Nutzt dieselbe Token-Auth wie die übrige
Kunden-API (/api/*)."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from ..api_auth import require_api_token
from ..catalog import scan_photo_folders
from ..database import get_db
from ..uploads import delete_upload, get_quota_usage, save_photo_upload, save_upload

router = APIRouter(prefix="/api")


def _require_upload_folder(user) -> str:
    folder = user["upload_folder"]
    if not folder:
        raise HTTPException(status_code=403, detail="Für diesen Kunden ist kein Upload freigeschaltet.")
    return folder


@router.get("/upload")
def list_uploads(user=Depends(require_api_token)):
    return get_quota_usage(_require_upload_folder(user), user["upload_quota_bytes"])


@router.post("/upload")
async def upload_file(file: UploadFile, user=Depends(require_api_token)):
    folder = _require_upload_folder(user)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Datei ist leer.")
    return save_upload(folder, file.filename or "datei", content, user["upload_quota_bytes"])


@router.post("/photo-upload")
async def upload_photo(file: UploadFile, user=Depends(require_api_token)):
    """Speichert ein Galerie-Foto nach <Kundenordner>/fotos/uploads und gibt es frei."""
    folder = _require_upload_folder(user)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Datei ist leer.")
    saved = save_photo_upload(folder, file.filename or "foto", content, user["upload_quota_bytes"])
    photo_folder = f"{folder}/fotos/uploads"
    scan_photo_folders([photo_folder])
    with get_db() as conn:
        photo = conn.execute("SELECT id FROM photos WHERE filepath = ?", (saved["filepath"],)).fetchone()
        if not photo:
            raise HTTPException(status_code=500, detail="Foto konnte nicht katalogisiert werden.")
        conn.execute("INSERT OR IGNORE INTO photo_permissions (user_id, photo_id) VALUES (?, ?)", (user["id"], photo["id"]))
    return saved


@router.delete("/upload/{filename}")
def delete_uploaded_file(filename: str, user=Depends(require_api_token)):
    delete_upload(_require_upload_folder(user), filename)
    return {"ok": True}
