"""Datei-Upload für Kunden (Bilder/kleine Videos, Kontingent individuell pro
Kunde einstellbar - siehe app/customers.py:set_upload_quota) - die eigentliche
Logik steckt in app/uploads.py. Nutzt dieselbe Token-Auth wie die übrige
Kunden-API (/api/*)."""

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from ..api_auth import require_api_token
from ..catalog import scan_library, scan_photo_folders
from ..database import get_db
from ..uploads import delete_upload, get_quota_usage, save_customer_upload

router = APIRouter(prefix="/api")


def _all_upload_folders(user) -> list[str]:
    return [
        user[key]
        for key in ("photo_upload_folder", "video_upload_folder")
        if key in user.keys() and user[key]
    ]


@router.get("/upload")
def list_uploads(user=Depends(require_api_token)):
    folders = _all_upload_folders(user)
    if not folders:
        raise HTTPException(status_code=403, detail="Für diesen Kunden ist kein konkreter Upload-Zielordner freigeschaltet.")
    return get_quota_usage(folders, user["upload_quota_bytes"])


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    area: str = Form(...),
    upload_name: str = Form(..., min_length=1, max_length=80),
    user=Depends(require_api_token),
):
    saved = await save_customer_upload(user, area, upload_name, file)
    if area == "photo":
        scan_photo_folders([str(saved["filepath"]).rsplit("/", 1)[0]])
        with get_db() as conn:
            photo = conn.execute("SELECT id FROM photos WHERE filepath = ?", (saved["filepath"],)).fetchone()
            if not photo:
                raise HTTPException(status_code=500, detail="Foto konnte nicht katalogisiert werden.")
            conn.execute("INSERT OR IGNORE INTO photo_permissions (user_id, photo_id) VALUES (?, ?)", (user["id"], photo["id"]))
    else:
        scan_library([str(saved["filepath"]).rsplit("/", 1)[0]])
        with get_db() as conn:
            video = conn.execute("SELECT id FROM videos WHERE filepath = ?", (saved["filepath"],)).fetchone()
            if not video:
                raise HTTPException(status_code=500, detail="Video konnte nicht katalogisiert werden.")
            conn.execute("INSERT OR IGNORE INTO permissions (user_id, video_id) VALUES (?, ?)", (user["id"], video["id"]))
    return saved


@router.delete("/upload/{filename}")
def delete_uploaded_file(filename: str, user=Depends(require_api_token)):
    delete_upload(_all_upload_folders(user), filename)
    return {"ok": True}
