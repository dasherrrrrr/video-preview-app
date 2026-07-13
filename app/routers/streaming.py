import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import require_login
from ..catalog import VIDEOS_DIR
from ..media import get_authorized_video

router = APIRouter()

CHUNK_SIZE = 1024 * 1024  # 1 MB pro gelesenem Block
RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


@router.get("/stream/{video_id}")
def stream_video(video_id: int, request: Request, user=Depends(require_login)):
    video = get_authorized_video(video_id, user)
    filepath = VIDEOS_DIR / video["filepath"]
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht (mehr) vorhanden.")

    file_size = filepath.stat().st_size
    range_header = request.headers.get("range")

    start, end = 0, file_size - 1
    status_code = 200
    if range_header:
        match = RANGE_RE.match(range_header)
        if not match:
            raise HTTPException(status_code=416, detail="Ungültiger Range-Header.")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else file_size - 1
        end = min(end, file_size - 1)
        if start >= file_size or start > end:
            raise HTTPException(status_code=416, detail="Range außerhalb der Dateigröße.")
        status_code = 206

    length = end - start + 1

    def iterfile():
        with open(filepath, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    return StreamingResponse(
        iterfile(), status_code=status_code, headers=headers, media_type="video/mp4"
    )
