"""Gemeinsame Berechtigungsprüfung und Range-Streaming-Logik für Player-Seite,
Session-Streaming-Endpoint und Token-basierten API-Endpoint."""

import re
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .database import get_db

CHUNK_SIZE = 1024 * 1024  # 1 MB pro gelesenem Block
RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def get_authorized_video(video_id: int, user):
    with get_db() as conn:
        video = conn.execute(
            "SELECT id, filepath, title, duration_seconds, codec FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if not video:
            raise HTTPException(status_code=404, detail="Video nicht gefunden.")
        if not user["is_admin"]:
            allowed = conn.execute(
                "SELECT 1 FROM permissions WHERE user_id = ? AND video_id = ?",
                (user["id"], video_id),
            ).fetchone()
            if not allowed:
                raise HTTPException(status_code=403, detail="Kein Zugriff auf dieses Video.")
    return video


def build_stream_response(
    filepath: Path, range_header: Optional[str], extra_headers: Optional[dict] = None
) -> StreamingResponse:
    """Baut eine Range-fähige StreamingResponse für eine Videodatei - von
    /stream (Session-Login) und /api/stream (Token-Login) gemeinsam genutzt,
    damit die Range-Parsing-Logik nicht doppelt gepflegt werden muss."""
    file_size = filepath.stat().st_size
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

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    if extra_headers:
        headers.update(extra_headers)

    return StreamingResponse(
        iterfile(), status_code=status_code, headers=headers, media_type="video/mp4"
    )
