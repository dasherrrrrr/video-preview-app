from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..auth import require_login
from ..catalog import VIDEOS_DIR
from ..media import build_stream_response, get_authorized_video
from ..thumbnails import get_thumbnail_path
from ..transcode import ensure_transcoded, needs_transcode

router = APIRouter()


@router.get("/thumbnail/{video_id}")
def thumbnail(video_id: int, user=Depends(require_login)):
    get_authorized_video(video_id, user)
    path = get_thumbnail_path(video_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Kein Vorschaubild vorhanden.")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/stream/{video_id}")
def stream_video(video_id: int, request: Request, user=Depends(require_login)):
    video = get_authorized_video(video_id, user)
    filepath = VIDEOS_DIR / video["filepath"]
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht (mehr) vorhanden.")

    if needs_transcode(video["codec"]):
        try:
            filepath = ensure_transcoded(video_id, filepath)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return build_stream_response(filepath, request.headers.get("range"))
