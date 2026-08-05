"""Datei-Upload für Kunden (Bilder/kleine Videos, Kontingent individuell pro
Kunde einstellbar - siehe app/customers.py:set_upload_quota) - die eigentliche
Logik steckt in app/uploads.py. Nutzt dieselbe Token-Auth wie die übrige
Kunden-API (/api/*)."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from ..api_auth import require_api_token
from ..uploads import delete_upload, get_quota_usage, save_upload

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


@router.delete("/upload/{filename}")
def delete_uploaded_file(filename: str, user=Depends(require_api_token)):
    delete_upload(_require_upload_folder(user), filename)
    return {"ok": True}
