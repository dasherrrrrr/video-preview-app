from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..branding import get_branding_content_type, get_branding_path, has_branding

router = APIRouter()


@router.get("/branding/{kind}")
def serve_branding(kind: str):
    if kind not in ("logo", "favicon") or not has_branding(kind):
        raise HTTPException(status_code=404, detail="Nicht gesetzt.")
    content_type = get_branding_content_type(kind) or "application/octet-stream"
    return FileResponse(get_branding_path(kind), media_type=content_type)
