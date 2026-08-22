"""Gemeinsame Berechtigungsprüfung und Range-Streaming-Logik für Player-Seite,
Session-Streaming-Endpoint und Token-basierten API-Endpoint."""

import mimetypes
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .database import get_db
from .settings import get_setting

CHUNK_SIZE = 1024 * 1024  # 1 MB pro gelesenem Block
DOWNLOAD_CHUNK_SIZE = 64 * 1024  # kleiner für feinere Bandbreiten-Drosselung
RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def get_authorized_video(video_id: int, user):
    with get_db() as conn:
        video = conn.execute(
            "SELECT id, filepath, title, duration_seconds, codec, bit_rate, width FROM videos WHERE id = ?",
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

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        # Ohne das cachen Browser Video-Byteranges nach eigenem Ermessen -
        # wird die Datei hinter derselben URL später neu erzeugt (z.B. nach
        # einem Transcode-Fix), bekommt man sonst weiter die alten,
        # zwischengespeicherten Bytes ausgeliefert, ohne dass ein Reload das
        # Video selbst neu lädt.
        "Cache-Control": "no-store",
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    if extra_headers:
        headers.update(extra_headers)

    return StreamingResponse(
        iterfile(), status_code=status_code, headers=headers, media_type="video/mp4"
    )


def build_download_response(filepath: Path, filename: str) -> StreamingResponse:
    """Baut eine Download-Response (Content-Disposition: attachment) für das
    Original in voller Qualität - mit optionaler Bandbreiten-Drosselung aus
    den Admin-Einstellungen (/admin/settings), damit ein einzelner Download
    nicht die ganze Upload-Leitung des Servers sättigt. Kein Range-Support -
    ein unterbrochener Download muss von vorn beginnen, das ist hier ok."""
    file_size = filepath.stat().st_size
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    safe_filename = filename.replace('"', "")

    raw_limit = get_setting("download_bandwidth_mbps", "").strip().replace(",", ".")
    bytes_per_sec = None
    try:
        mbps = float(raw_limit)
        if mbps > 0:
            bytes_per_sec = int(mbps * 1_000_000 / 8)  # Mbit/s -> Byte/s
    except ValueError:
        pass

    def iterfile():
        with open(filepath, "rb") as f:
            start = time.monotonic()
            sent = 0
            while True:
                chunk = f.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                sent += len(chunk)
                yield chunk
                if bytes_per_sec:
                    expected = sent / bytes_per_sec
                    elapsed = time.monotonic() - start
                    if expected > elapsed:
                        time.sleep(expected - elapsed)

    headers = {
        "Content-Length": str(file_size),
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
    }
    return StreamingResponse(iterfile(), media_type=media_type, headers=headers)
